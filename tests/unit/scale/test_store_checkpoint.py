"""The key-value stores' contract, RadexStore's mapping of RaDEX, and the
checkpointer over a store."""

import sys
import types

import pytest
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from flowgentic.langgraph import KeyValueSaver
from flowgentic.store import KeyValueStore, MemoryStore, RadexStore


class FakeRadex:
    """Stands in for RaDEX's Python client: picklables, tensors, KeyNotFoundError."""

    class KeyNotFoundError(Exception):
        pass

    def __init__(self):
        self.data = {}
        self.deletes_of_missing = 0
        self.clients = 0

    def install(self, monkeypatch):
        fake = self

        class DragonClient:
            def __init__(self, descriptor, timeout):
                fake.clients += 1

            def get_picklable(self, key):
                if key not in fake.data:
                    raise FakeRadex.KeyNotFoundError(key)
                return fake.data[key]

            def put_picklable(self, key, value):
                fake.data[key] = value

            def contains(self, key):
                return key in fake.data

            def delete_item(self, handle):
                if handle.key not in fake.data:
                    fake.deletes_of_missing += 1  # RaDEX blocks here for the timeout
                fake.data.pop(handle.key, None)

            def put_tensor(self, handle, array):
                fake.data[handle.key] = array

            def get_tensor(self, handle):
                if handle.key not in fake.data:
                    raise FakeRadex.KeyNotFoundError(handle.key)
                return fake.data[handle.key]

        class Handle:
            def __init__(self, key):
                self.key = key

        modules = {
            "radex": types.ModuleType("radex"),
            "radex.clients": types.ModuleType("radex.clients"),
            "radex.clients.core": types.SimpleNamespace(DragonClient=DragonClient),
            "radex.exceptions": types.SimpleNamespace(KeyNotFoundError=FakeRadex.KeyNotFoundError),
            "radex.handles": types.ModuleType("radex.handles"),
            "radex.handles.handles": types.SimpleNamespace(
                IncomingHandle=Handle, OutgoingHandle=Handle
            ),
        }
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr(RadexStore, "_clients", {})


@pytest.fixture(params=["memory", "radex"])
def store(request, monkeypatch):
    if request.param == "memory":
        return MemoryStore()
    fake = FakeRadex()
    fake.install(monkeypatch)
    s = RadexStore.attach("descriptor")
    s.fake = fake
    return s


def test_store_contract(store):
    assert isinstance(store, KeyValueStore)
    with pytest.raises(KeyError):
        store.get("missing")
    assert store.get("missing", None) is None
    store.put("k", {"x": [1, 2]})
    assert store.get("k") == {"x": [1, 2]} and store.contains("k")
    store.delete("k")
    store.delete("k")  # a missing key is not an error
    assert not store.contains("k")
    store.put_tensor("t", [1.0, 2.0])
    assert list(store.get_tensor("t")) == [1.0, 2.0]
    if isinstance(store, RadexStore):
        assert store.fake.deletes_of_missing == 0  # checked before deleting
        with pytest.raises(KeyError):
            store.get_tensor("missing")


def test_radex_attach_is_once_per_descriptor(monkeypatch):
    fake = FakeRadex()
    fake.install(monkeypatch)
    assert RadexStore.attach("d1") is RadexStore.attach("d1")
    assert RadexStore.attach("d2") is not RadexStore.attach("d1")
    assert fake.clients == 2


def test_radex_missing_says_how_to_get_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "radex.clients.core", None)
    with pytest.raises(ImportError, match="built from source"):
        RadexStore("descriptor")


def graph(saver, stop_inside: bool = False):
    async def a(state: MessagesState):
        return {"messages": [("ai", "a")]}

    async def b(state: MessagesState):
        if stop_inside:
            value = interrupt({"why": "wait"})
            return {"messages": [("ai", f"b {value}")]}
        return {"messages": [("ai", "b")]}

    g = StateGraph(MessagesState)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile(checkpointer=saver, interrupt_before=[] if stop_inside else ["b"])


@pytest.mark.parametrize("stop_inside", [False, True])
async def test_an_interrupted_graph_resumes_from_the_store(store, stop_inside):
    saver = KeyValueSaver(store)
    config = {"configurable": {"thread_id": "agent-1"}}
    out = await graph(saver, stop_inside).ainvoke(
        {"messages": [("human", "hi")]}, config, durability="exit"
    )
    assert [m.content for m in out["messages"]] == ["hi", "a"]
    fresh = graph(KeyValueSaver(store), stop_inside)  # nothing carries over in memory
    assert (await fresh.aget_state(config)).next == ("b",)
    resume = Command(resume="done") if stop_inside else None
    out = await fresh.ainvoke(resume, config, durability="exit")
    assert out["messages"][-1].content == ("b done" if stop_inside else "b")
    assert saver.checkpoints_put == 1


async def test_retention_list_and_delete():
    store = MemoryStore()
    saver = KeyValueSaver(store, keep_last=2)
    config = {"configurable": {"thread_id": "t"}}
    g = graph(saver)
    await g.ainvoke({"messages": [("human", "hi")]}, config, durability="sync")
    await g.ainvoke(None, config, durability="sync")
    items = list(saver.list(config))
    assert len(items) == 2  # keep_last
    assert items[0].checkpoint["id"] > items[1].checkpoint["id"]  # newest first
    assert len(list(saver.list(config, limit=1))) == 1
    assert list(saver.list(config, before=items[0].config)) == items[1:]
    with pytest.raises(NotImplementedError, match="thread_id"):
        list(saver.list(None))
    saver.delete_thread("t")
    assert store.keys() == []


def test_writes_are_kept_once_and_special_writes_replace():
    saver = KeyValueSaver(MemoryStore())
    config = {"configurable": {"thread_id": "t", "checkpoint_ns": "", "checkpoint_id": "c1"}}
    saver.put_writes(config, [("x", 1)], task_id="task")
    saver.put_writes(config, [("x", 2)], task_id="task")  # a regular write: kept once
    saver.put_writes(config, [("__error__", "e1")], task_id="task")
    saver.put_writes(config, [("__error__", "e2")], task_id="task")  # special: replaces
    stored = saver.store.get("lg/t//w/c1")
    values = [(channel, saver.serde.loads_typed(value)) for _, channel, value, _, _ in stored]
    assert values == [("x", 1), ("__error__", "e2")]
