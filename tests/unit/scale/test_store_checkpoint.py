"""The key-value stores' contract, RadexStore's mapping of RaDEX, and the
checkpointer over a store."""

import sys
import types
from collections import Counter

import pytest
from langgraph.checkpoint.base import empty_checkpoint
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
			"radex.exceptions": types.SimpleNamespace(
				KeyNotFoundError=FakeRadex.KeyNotFoundError
			),
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


class BlockImport:
	"""A finder that makes one module unimportable, as if it were not installed."""

	def __init__(self, name: str) -> None:
		self.name = name

	def find_spec(self, fullname, path, target=None):
		if fullname == self.name:
			raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)


def block(monkeypatch, name: str) -> None:
	for loaded in [m for m in sys.modules if m == "radex" or m.startswith("radex.")]:
		monkeypatch.delitem(sys.modules, loaded)
	monkeypatch.setattr(sys, "meta_path", [BlockImport(name), *sys.meta_path])


def test_radex_missing_says_how_to_get_it(monkeypatch):
	block(monkeypatch, "radex")
	with pytest.raises(ModuleNotFoundError, match="built from source") as raised:
		RadexStore("descriptor")
	assert raised.value.name == "radex"


def test_a_partial_radex_build_keeps_its_own_error(monkeypatch):
	fake = FakeRadex()
	fake.install(monkeypatch)
	monkeypatch.setitem(sys.modules, "radex.clients.core", None)
	with pytest.raises(ModuleNotFoundError, match="radex.clients.core") as raised:
		RadexStore("descriptor")
	assert "built from source" not in str(raised.value)


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


def checkpoint(saver, checkpoint_id: str, parent: str | None = None) -> dict:
	"""Put an empty checkpoint with this id; return its config."""
	configurable = {"thread_id": "t", "checkpoint_ns": ""}
	if parent:
		configurable["checkpoint_id"] = parent
	cp = empty_checkpoint()
	cp["id"] = checkpoint_id
	return saver.put({"configurable": configurable}, cp, {"step": 0}, {})


def pending(saver, config) -> list:
	return sorted(
		(channel, value) for _, channel, value in saver.get_tuple(config).pending_writes
	)


def test_writes_are_kept_once_and_special_writes_replace():
	saver = KeyValueSaver(MemoryStore())
	config = checkpoint(saver, "c1")
	saver.put_writes(config, [("x", 1)], task_id="task")
	saver.put_writes(config, [("x", 2)], task_id="task")  # a regular write: kept once
	saver.put_writes(config, [("__error__", "e1")], task_id="task")
	saver.put_writes(config, [("__error__", "e2")], task_id="task")  # special: replaces
	saver.put_writes(config, [("y", 3)], task_id="other")
	assert pending(saver, config) == [("__error__", "e2"), ("x", 1), ("y", 3)]
	# a saver in another process, which has not seen these writes, adds to them
	again = KeyValueSaver(saver.store)
	again.put_writes(config, [("z", 4)], task_id="third")
	assert pending(again, config) == [("__error__", "e2"), ("x", 1), ("y", 3), ("z", 4)]


def test_a_new_checkpoint_has_none_of_the_previous_ones_writes():
	for keep_last in (1, 2):
		saver = KeyValueSaver(MemoryStore(), keep_last=keep_last)
		first = checkpoint(saver, "c1")
		saver.put_writes(first, [("x", 1)], task_id="task")
		second = checkpoint(saver, "c2", parent="c1")
		latest = {"configurable": {"thread_id": "t"}}
		assert saver.get_tuple(latest).checkpoint["id"] == "c2"
		assert pending(saver, latest) == [] and pending(saver, second) == []
		if keep_last == 2:  # the older one keeps its writes, and takes new ones
			saver.put_writes(first, [("y", 2)], task_id="late")
			assert pending(saver, first) == [("x", 1), ("y", 2)]
			assert pending(saver, second) == []
		else:
			assert saver.get_tuple(first) is None
			saver.put_writes(second, [("z", 3)], task_id="task")
			saver.put_writes(first, [("y", 2)], task_id="late")  # not kept: dropped
			assert pending(saver, second) == [("z", 3)]


def test_namespaces_are_listed_and_deleted_with_the_thread():
	store = MemoryStore()
	saver = KeyValueSaver(store)
	checkpoint(saver, "c1")
	sub = {"configurable": {"thread_id": "t", "checkpoint_ns": "child:1"}}
	cp = empty_checkpoint()
	cp["id"] = "s1"
	saver.put(sub, cp, {"step": 0}, {})
	listed = [
		(item.config["configurable"]["checkpoint_ns"], item.checkpoint["id"])
		for item in saver.list({"configurable": {"thread_id": "t"}})
	]
	assert listed == [("child:1", "s1"), ("", "c1")]
	assert next(saver.list({"configurable": {"thread_id": "t"}}, limit=1)).checkpoint["id"] == "s1"
	saver.delete_thread("t")
	assert store.keys() == []


class CountingStore(MemoryStore):
	"""Counts the store calls, by operation."""

	def __init__(self):
		super().__init__()
		self.calls = Counter()

	def get(self, key, *default):
		self.calls["get"] += 1
		return super().get(key, *default)

	def put(self, key, value):
		self.calls["put"] += 1
		super().put(key, value)

	def delete(self, key):
		self.calls["delete"] += 1
		super().delete(key)

	def contains(self, key):
		self.calls["contains"] += 1
		return super().contains(key)

	def take(self) -> dict:
		calls, self.calls = dict(self.calls), Counter()
		return calls


async def test_an_agent_that_suspends_costs_six_store_calls_per_suspension():
	"""Each store call is a round trip that blocks the event loop, and RaDEX's
	client grows with each. A suspension writes the checkpoint and its pending
	writes, and the resume reads both back; nothing is deleted."""
	store = CountingStore()
	config = {"configurable": {"thread_id": "agent-1"}}
	g = graph(KeyValueSaver(store), stop_inside=True)
	await g.ainvoke({"messages": [("human", "hi")]}, config, durability="exit")
	assert store.take() == {
		"get": 3,
		"put": 2,
	}  # no checkpoint yet; then the suspension
	await g.ainvoke(Command(resume="done"), config, durability="exit")
	assert store.take() == {
		"get": 2,
		"put": 1,
	}  # the resume's read; the final checkpoint
	await g.ainvoke({"messages": [("human", "again")]}, config, durability="exit")
	await g.ainvoke(Command(resume="done"), config, durability="exit")
	assert store.take() == {"get": 6, "put": 3}  # the same, on a thread with history


@pytest.mark.parametrize("keep_last", [1, 2])
def test_reopened_savers_merge_writes_and_ignore_evicted_checkpoints(keep_last):
	store = MemoryStore()
	first = KeyValueSaver(store, keep_last=keep_last)
	second = KeyValueSaver(store, keep_last=keep_last)
	c1 = checkpoint(first, "c1")
	second.put_writes(c1, [("x", 1)], "one")
	first.put_writes(c1, [("y", 2)], "two")
	checkpoint(first, "c1")  # retrying the put preserves both task writes
	first.put_writes(c1, [("z", 3)], "three")
	assert pending(second, c1) == [("x", 1), ("y", 2), ("z", 3)]
	c2 = checkpoint(second, "c2")
	second.put_writes(c2, [("current", 4)], "four")
	first.put_writes(c1, [("late", 5)], "five")
	assert pending(first, c2) == [("current", 4)]
	if keep_last == 2:
		assert ("late", 5) in pending(second, c1)


def test_writes_can_arrive_before_their_checkpoint():
	saver = KeyValueSaver(MemoryStore())
	c1 = checkpoint(saver, "c1")
	c2 = saver._config("t", "", "c2")
	saver.put_writes(c2, [("future", 1)], "one")
	saver.put_writes(c1, [("current", 2)], "two")
	assert pending(saver, c1) == [("current", 2)]
	checkpoint(saver, "c2", parent="c1")
	assert pending(saver, c2) == [("future", 1)]


def test_thread_and_namespace_separators_cannot_alias():
	saver = KeyValueSaver(MemoryStore())
	configs = []
	for thread, ns, value in [("a/b", "c", "one"), ("a", "b/c", "two"), ("a%2Fb", "c", "three")]:
		cp = empty_checkpoint()
		cp["id"] = value
		configs.append(saver.put({"configurable": {"thread_id": thread, "checkpoint_ns": ns}}, cp, {}, {}))
	saver.delete_thread("a")
	assert [saver.get_tuple(c).checkpoint["id"] for c in configs[::2]] == ["one", "three"]
	assert saver.get_tuple(configs[1]) is None


@pytest.mark.parametrize("durability", ["sync", "exit"])
async def test_delta_state_survives_pruning_and_reopening(durability):
	from typing import Annotated, TypedDict

	DeltaChannel = pytest.importorskip("langgraph.channels.delta").DeltaChannel

	def accumulate(state, batches):
		return state + [value for batch in batches for value in batch]

	class State(TypedDict):
		history: Annotated[list[str], DeltaChannel(accumulate, snapshot_frequency=3)]

	builder = StateGraph(State)
	builder.add_node("append", lambda state: {"history": ["node"]})
	builder.add_edge(START, "append")
	builder.add_edge("append", END)
	store = MemoryStore()
	config = {"configurable": {"thread_id": "delta"}}
	expected = []
	for label in ["one", "two", "three", "four"]:
		graph = builder.compile(checkpointer=KeyValueSaver(store))
		await graph.ainvoke({"history": [label]}, config, durability=durability)
		expected.extend([label, "node"])
		assert (await graph.aget_state(config)).values["history"] == expected


def test_parallel_writes_from_two_savers_are_merged():
	from concurrent.futures import ThreadPoolExecutor

	store = MemoryStore()
	savers = [KeyValueSaver(store), KeyValueSaver(store)]
	config = checkpoint(savers[0], "c1")

	def write(i):
		savers[i % 2].put_writes(config, [("output", i)], str(i))

	with ThreadPoolExecutor(max_workers=4) as pool:
		list(pool.map(write, range(32)))
	assert pending(savers[0], config) == [("output", i) for i in range(32)]


def test_delete_removes_early_writes_from_child_namespace():
	store = MemoryStore()
	saver = KeyValueSaver(store)
	config = {"configurable": {"thread_id": "t", "checkpoint_ns": "child", "checkpoint_id": "c"}}
	saver.put_writes(config, [("early", 1)], "task")
	saver.delete_thread("t")
	assert not store.keys()


async def test_write_through_suspend_resume_and_handoff():
	from flowgentic.store import WriteThroughStore

	backing = CountingStore()
	owned = WriteThroughStore(backing, max_bytes=1 << 20)
	g = graph(KeyValueSaver(owned), stop_inside=True)
	config = {"configurable": {"thread_id": "t"}}
	await g.ainvoke({"messages": [("human", "hi")]}, config, durability="exit")
	assert backing.take() == {"get": 2, "put": 2}
	await g.ainvoke(Command(resume="done"), config, durability="exit")
	assert backing.take() == {"put": 1}
	await g.ainvoke({"messages": [("human", "again")]}, config, durability="exit")
	assert backing.take() == {"put": 2}
	# A different owner receives persisted writes, even if the first disappears.
	new_owner = graph(KeyValueSaver(WriteThroughStore(backing, max_bytes=1 << 20)), True)
	result = await new_owner.ainvoke(Command(resume="moved"), config, durability="exit")
	assert result["messages"][-1].content == "b moved"
	assert backing.take() == {"get": 2, "put": 1}


def test_store_cache_is_bounded_and_values_are_isolated():
	from flowgentic.store import WriteThroughStore

	backing = CountingStore()
	cache = WriteThroughStore(backing, max_bytes=128, max_entries=2)
	cache.put("a", [1])
	cache.get("a").append(2)
	assert cache.get("a") == [1]
	cache.put("b", 2)
	cache.get("a")  # b is least recently used
	cache.put("c", 3)
	backing.take()
	assert cache.get("b") == 2 and backing.take() == {"get": 1}
	cache.put("large", bytes(1024))
	backing.take()
	assert cache.get("large") == bytes(1024) and backing.take() == {"get": 1}
	cache.delete("b")
	assert not cache.contains("b") and cache.get("b", 42) == 42
	with pytest.raises(KeyError):
		cache.get("b")


def test_failed_backing_write_invalidates_cache():
	from flowgentic.store import WriteThroughStore

	class Uncertain(MemoryStore):
		def put(self, key, value):
			super().put(key, value)
			if value == 2:
				raise OSError("lost acknowledgement")

	cache = WriteThroughStore(Uncertain(), max_bytes=1024)
	cache.put("k", 1)
	with pytest.raises(OSError, match="acknowledgement"):
		cache.put("k", 2)
	assert cache.get("k") == 2


def test_store_cache_preserves_values_supported_only_by_the_backend():
	from flowgentic.store import WriteThroughStore

	class ExtendedStore(dict):
		put = dict.__setitem__

	backing = ExtendedStore()
	cache = WriteThroughStore(backing, max_bytes=128)
	cache.put("item", "old")
	cache.put("item", lambda: "new")
	assert backing["item"]() == cache.get("item")() == "new"


def test_cache_evicts_on_total_value_bytes():
	from flowgentic.store import WriteThroughStore

	backing = CountingStore()
	cache = WriteThroughStore(backing, max_bytes=100)
	for key in ("a", "b"):
		cache.put(key, bytes(64))
	backing.take()
	assert cache.get("b") == bytes(64) and backing.take() == {}
	assert cache.get("a") == bytes(64) and backing.take() == {"get": 1}
