"""The key-value stores' contract, RadexStore's mapping of RaDEX, and the
checkpointer over a store."""

import asyncio
import operator
import os
import sys
import time
import types
from collections import Counter
from typing import Annotated, TypedDict

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


@pytest.fixture(
	params=["memory", "radex", pytest.param("dragon", marks=pytest.mark.dragon)]
)
def store(request, monkeypatch):
	if request.param == "memory":
		return MemoryStore()
	if request.param == "dragon":  # RaDEX itself, on a Dragon dictionary
		return RadexStore.attach(request.getfixturevalue("dragon_descriptor"))
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
	t0 = time.monotonic()
	store.delete("k")  # a missing key is not an error
	assert time.monotonic() - t0 < 1.0  # nor a wait for RaDEX's timeout
	assert not store.contains("k")
	store.put_tensor("t", [1.0, 2.0])
	assert list(store.get_tensor("t")) == [1.0, 2.0]
	if isinstance(store, RadexStore):
		if hasattr(store, "fake"):
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


def agent_config(i: int) -> dict:
	return {"configurable": {"thread_id": f"agent-{i}"}}


def suspend_agents(descriptor: str, agents: int, out) -> None:
	"""A Dragon process: run each agent until it suspends, then exit."""

	async def run():
		saver = KeyValueSaver(RadexStore.attach(descriptor))
		g = graph(saver, stop_inside=True)
		outs = await asyncio.gather(
			*(
				g.ainvoke(
					{"messages": [("human", f"hi {i}")]},
					agent_config(i),
					durability="exit",
				)
				for i in range(agents)
			)
		)
		return {
			"pid": os.getpid(),
			"suspended": sum("__interrupt__" in o for o in outs),
			"checkpoints_put": saver.checkpoints_put,
		}

	out.put(asyncio.run(run()))


def resume_agents(descriptor: str, agents: int, out) -> None:
	"""A second Dragon process: find each agent suspended in the store, and
	resume it to its end."""

	async def run():
		saver = KeyValueSaver(RadexStore.attach(descriptor))
		g = graph(saver, stop_inside=True)
		waiting = [(await g.aget_state(agent_config(i))).next for i in range(agents)]
		outs = await asyncio.gather(
			*(
				g.ainvoke(
					Command(resume=f"done {i}"), agent_config(i), durability="exit"
				)
				for i in range(agents)
			)
		)
		return {
			"pid": os.getpid(),
			"waiting_at": sorted(set(waiting)),
			"answers": [[m.content for m in o["messages"]] for o in outs],
			"checkpoints_put": saver.checkpoints_put,
		}

	out.put(asyncio.run(run()))


@pytest.mark.dragon
def test_an_agent_suspended_by_one_dragon_process_resumes_in_another(dragon_descriptor):
	"""Agents suspend in one Dragon process, which then exits; a second process
	finds them in the dictionary and resumes them."""
	from _dragon import dragon_queue, join, start, take

	agents = 32
	out = dragon_queue()
	first = start(suspend_agents, dragon_descriptor, agents, out)
	(suspended,) = take(out, 1)
	join(first)  # nothing of the agents is left in a process
	second = start(resume_agents, dragon_descriptor, agents, out)
	(resumed,) = take(out, 1)
	join(second)
	assert len({suspended["pid"], resumed["pid"], os.getpid()}) == 3
	assert suspended["suspended"] == agents
	assert resumed["waiting_at"] == [("b",)]
	assert resumed["answers"] == [
		["hi " + str(i), "a", f"b done {i}"] for i in range(agents)
	]
	# With exit durability: one checkpoint at the suspension, one at the end.
	assert suspended["checkpoints_put"] == resumed["checkpoints_put"] == agents
	final = KeyValueSaver(RadexStore.attach(dragon_descriptor))
	assert (
		final.get_tuple(agent_config(0))
		.checkpoint["channel_values"]["messages"][-1]
		.content
		== "b done 0"
	)


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
	assert (
		next(saver.list({"configurable": {"thread_id": "t"}}, limit=1)).checkpoint["id"]
		== "s1"
	)
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
	for thread, ns, value in [
		("a/b", "c", "one"),
		("a", "b/c", "two"),
		("a%2Fb", "c", "three"),
	]:
		cp = empty_checkpoint()
		cp["id"] = value
		configs.append(
			saver.put(
				{"configurable": {"thread_id": thread, "checkpoint_ns": ns}}, cp, {}, {}
			)
		)
	saver.delete_thread("a")
	assert [saver.get_tuple(c).checkpoint["id"] for c in configs[::2]] == [
		"one",
		"three",
	]
	assert saver.get_tuple(configs[1]) is None


@pytest.mark.parametrize("durability", ["sync", "async", "exit"])
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
	config = {
		"configurable": {
			"thread_id": "t",
			"checkpoint_ns": "child",
			"checkpoint_id": "c",
		}
	}
	saver.put_writes(config, [("early", 1)], "task")
	saver.delete_thread("t")
	assert not store.keys()


def test_a_clone_with_an_allowlist_counts_on_the_saver():
	"""With LANGGRAPH_STRICT_MSGPACK set, a compiled graph works on a shallow
	clone of its saver, with a stricter serializer: the saver the application
	holds must still count what the clone puts and reads."""
	from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

	strict = JsonPlusSerializer(allowed_msgpack_modules=None)
	saver = KeyValueSaver(MemoryStore(), serde=strict)
	clone = saver.with_allowlist([("flowgentic.tests", "Nothing")])
	assert clone is not saver
	config = checkpoint(clone, "c1")
	clone.put_writes(config, [("x", 1)], "task")
	assert clone.get_tuple(config) is not None
	stats = saver.stats()
	assert stats["checkpoints_put"] == 1
	assert stats["writes_put"] == 1
	assert stats["checkpoints_read"] == 1
	assert stats["checkpoint_bytes_mean"] > 0
	assert clone.stats() == stats


def test_a_strict_msgpack_graph_counts_on_the_saver():
	"""The same, end to end, in an interpreter with LANGGRAPH_STRICT_MSGPACK."""
	import subprocess
	import textwrap

	code = textwrap.dedent("""
		import asyncio
		from langgraph.graph import START, MessagesState, StateGraph
		from flowgentic.langgraph import KeyValueSaver
		from flowgentic.store import MemoryStore

		saver = KeyValueSaver(MemoryStore())
		g = StateGraph(MessagesState)
		g.add_node("a", lambda s: {"messages": [("ai", "a")]})
		g.add_edge(START, "a")
		graph = g.compile(checkpointer=saver)
		config = {"configurable": {"thread_id": "t"}}
		asyncio.run(graph.ainvoke({"messages": [("human", "go")]}, config))
		print(saver.stats()["checkpoints_put"])
	""")
	env = {**os.environ, "LANGGRAPH_STRICT_MSGPACK": "true"}
	result = subprocess.run(
		[sys.executable, "-c", code],
		capture_output=True,
		text=True,
		env=env,
		check=False,
	)
	assert result.returncode == 0, result.stderr
	assert int(result.stdout.strip()) > 0


# --- subgraph namespaces ----------------------------------------------------------


def supervisor(saver, turns: int, nested: bool = False):
	"""A graph that runs a subgraph which suspends, ``turns`` times in a loop,
	as a supervisor does; with ``nested``, the suspension is one level deeper."""

	def work(state: MessagesState):
		value = interrupt({"wait": len(state["messages"])})
		return {"messages": [("ai", f"{'x' * 200} {value}")]}

	sub = StateGraph(MessagesState)
	sub.add_node("work", work)
	sub.add_edge(START, "work")
	sub.add_edge("work", END)
	sub = sub.compile()
	if nested:
		mid = StateGraph(MessagesState)
		mid.add_node("inner", sub)
		mid.add_edge(START, "inner")
		mid.add_edge("inner", END)
		sub = mid.compile()
	g = StateGraph(MessagesState)
	g.add_node("sub", sub)
	g.add_edge(START, "sub")
	g.add_conditional_edges(
		"sub", lambda s: "sub" if len(s["messages"]) <= turns else END, ["sub", END]
	)
	return g.compile(checkpointer=saver)


async def run_supervisor(saver, turns, nested, durability, config=None):
	g = supervisor(saver, turns, nested)
	config = config or {"configurable": {"thread_id": "t"}}
	out = await g.ainvoke(
		{"messages": [("human", "go")]}, config, durability=durability
	)
	resumes = 0
	while "__interrupt__" in out:
		resumes += 1
		out = await g.ainvoke(Command(resume=resumes), config, durability=durability)
	return [m.content for m in out["messages"]], resumes


@pytest.mark.parametrize("nested", [False, True], ids=["subgraph", "nested"])
@pytest.mark.parametrize("durability", ["exit", "async", "sync"])
async def test_a_supervisor_loop_keeps_only_its_live_subgraphs(durability, nested):
	"""Each pass of a subgraph has its own namespace. Once the parent has moved
	past the step that ran it, its checkpoints are dropped, so the store holds a
	few namespaces however long the loop runs, and the graph runs as on
	LangGraph's own saver."""
	from langgraph.checkpoint.memory import InMemorySaver

	kept = {}
	for turns in (5, 20):
		store = MemoryStore()
		out, resumes = await run_supervisor(
			KeyValueSaver(store), turns, nested, durability
		)
		assert resumes == turns
		reference, _ = await run_supervisor(InMemorySaver(), turns, nested, durability)
		assert out == reference
		registered = store.get("lg/t/namespaces")
		heads = {k.split("/")[2] for k in store.keys() if k.endswith("/head")}
		assert heads - {""} <= {_key_part(ns) for ns in registered}
		kept[turns] = (len(registered), len(store.keys()))
	assert kept[20] == kept[5]  # no more for a longer loop
	assert kept[20][0] <= (4 if nested else 2)


def _key_part(ns: str) -> str:
	return ns.replace("%", "%25").replace("/", "%2F")


def put_in(saver, ns: str, checkpoint_id: str, parents: dict[str, str] | None = None):
	configurable = {"thread_id": "t", "checkpoint_ns": ns}
	if parents is not None:
		configurable["checkpoint_map"] = parents
	cp = empty_checkpoint()
	cp["id"] = checkpoint_id
	return saver.put({"configurable": configurable}, cp, {"step": 0}, {})


def namespaces(saver) -> list[str]:
	return [ns for ns in saver._namespaces("t") if ns]


def test_a_subgraph_goes_once_its_parent_checkpoint_is_replaced():
	"""A subgraph's namespace is kept while the parent's stored checkpoint is
	the one it ran under, or a newer one has not been saved yet, as when a
	process stops between the child's save and the parent's."""
	saver = KeyValueSaver(MemoryStore())
	checkpoint(saver, "c1")
	put_in(saver, "sub:a", "s1", {"": "c1"})
	put_in(saver, "sub:a|inner:z", "i1", {"": "c1", "sub:a": "s1"})
	put_in(saver, "sub:b", "s2", {"": "c2"})  # the parent's c2 is not saved yet
	assert namespaces(saver) == ["sub:a", "sub:a|inner:z", "sub:b"]
	checkpoint(saver, "c2", parent="c1")
	put_in(saver, "sub:c", "s3", {"": "c3"})
	assert namespaces(saver) == ["sub:b", "sub:c"]  # sub:a and its child gone
	assert (
		saver.get_tuple(
			{"configurable": {"thread_id": "t", "checkpoint_ns": "sub:a|inner:z"}}
		)
		is None
	)
	assert (
		saver.get_tuple(
			{"configurable": {"thread_id": "t", "checkpoint_ns": "sub:b"}}
		).checkpoint["id"]
		== "s2"
	)


def test_subgraphs_of_retained_checkpoints_stay_with_keep_last():
	saver = KeyValueSaver(MemoryStore(), keep_last=2)
	checkpoint(saver, "c1")
	put_in(saver, "sub:a", "s1", {"": "c1"})
	checkpoint(saver, "c2", parent="c1")
	put_in(saver, "sub:b", "s2", {"": "c2"})
	assert namespaces(saver) == ["sub:a", "sub:b"]  # c1 is still retained
	checkpoint(saver, "c3", parent="c2")
	put_in(saver, "sub:c", "s3", {"": "c3"})
	assert namespaces(saver) == ["sub:b", "sub:c"]


def test_a_namespace_without_its_parents_checkpoint_id_is_kept():
	"""A namespace saved without LangGraph's map of parent checkpoints, or
	registered by 0.5.0 as a set, is dropped only with its thread."""
	store = MemoryStore()
	saver = KeyValueSaver(store)
	checkpoint(saver, "c1")
	put_in(saver, "sub:old", "s0")
	store.put("lg/t/namespaces", {"sub:old"} | set(store.get("lg/t/namespaces")))
	checkpoint(saver, "c2", parent="c1")
	put_in(saver, "sub:new", "s1", {"": "c3"})
	assert namespaces(saver) == ["sub:new", "sub:old"]
	saver.delete_thread("t")
	assert store.keys() == []


class Count(TypedDict):
	total: Annotated[int, operator.add]


class Log(TypedDict):
	log: Annotated[list, operator.add]


def with_memory(saver, turns: int, nested: bool):
	"""A supervisor loop that calls a stateful subgraph (``checkpointer=True``),
	which keeps one state across calls under a namespace without a task id,
	and a subgraph that suspends. With ``nested``, the stateful subgraph is
	called from inside a subgraph of its own."""
	mem = StateGraph(Count)
	mem.add_node("inc", lambda s: {"total": 1})
	mem.add_edge(START, "inc")
	mem.add_edge("inc", END)
	memory = mem.compile(checkpointer=True)

	def remember(state):
		return {"log": [("total", memory.invoke({"total": 0})["total"])]}

	if nested:
		inner = StateGraph(Log)
		inner.add_node("remember", remember)
		inner.add_edge(START, "remember")
		inner.add_edge("remember", END)
		team = inner.compile()

		def remember(state):  # noqa: F811
			return {"log": team.invoke({"log": []})["log"]}

	sus = StateGraph(Count)
	sus.add_node("wait", lambda s: {"total": interrupt({"wait": 1})})
	sus.add_edge(START, "wait")
	sus.add_edge("wait", END)
	worker = sus.compile()

	g = StateGraph(Log)
	g.add_node("remember", remember)
	g.add_node(
		"worker", lambda s: {"log": [("worker", worker.invoke({"total": 0})["total"])]}
	)
	g.add_edge(START, "remember")
	g.add_edge("remember", "worker")
	g.add_conditional_edges(
		"worker",
		lambda s: "remember" if len(s["log"]) < 2 * turns else END,
		["remember", END],
	)
	return g.compile(checkpointer=saver)


def totals(saver, turns, nested, durability) -> list[int]:
	g = with_memory(saver, turns, nested)
	config = {"configurable": {"thread_id": "t"}}
	out = g.invoke({"log": []}, config, durability=durability)
	while "__interrupt__" in out:
		out = g.invoke(Command(resume=1), config, durability=durability)
	return [value for key, value in out["log"] if key == "total"]


@pytest.mark.parametrize("nested", [False, True], ids=["subgraph", "nested"])
@pytest.mark.parametrize("durability", ["exit", "async", "sync"])
def test_a_stateful_subgraph_keeps_its_state_next_to_a_supervisor_loop(
	durability, nested
):
	"""A subgraph compiled with ``checkpointer=True`` saves every call under one
	namespace: dropping the namespaces of finished subgraph runs must never
	drop it."""
	from langgraph.checkpoint.memory import InMemorySaver

	got = totals(KeyValueSaver(MemoryStore()), 4, nested, durability)
	assert got == totals(InMemorySaver(), 4, nested, durability)
	assert got == [1, 2, 3, 4]


def test_only_a_namespace_named_after_its_task_is_dropped():
	"""A namespace whose last part has no task id (``name``, not ``name:id``)
	is a stateful subgraph's, reused on every call: it stays, at any depth."""
	saver = KeyValueSaver(MemoryStore())
	checkpoint(saver, "c1")
	put_in(saver, "memory", "m1", {"": "c1"})
	put_in(saver, "team:a", "s1", {"": "c1"})
	put_in(saver, "team:a|memory", "m2", {"": "c1", "team:a": "s1"})
	put_in(saver, "team:a|step:x", "x1", {"": "c1", "team:a": "s1"})
	saver.put(
		{
			"configurable": {
				"thread_id": "t",
				"checkpoint_ns": "team:a",
				"checkpoint_id": "s1",
			}
		},
		{**empty_checkpoint(), "id": "s2"},
		{"step": 1},
		{},
	)
	put_in(saver, "team:a|step:y", "y1", {"": "c1", "team:a": "s2"})
	assert namespaces(saver) == ["memory", "team:a", "team:a|memory", "team:a|step:y"]
	checkpoint(saver, "c2", parent="c1")
	put_in(saver, "team:b", "s3", {"": "c2"})
	assert namespaces(saver) == ["memory", "team:b"]  # team:a went with its subgraphs
	assert (
		saver.get_tuple(
			{"configurable": {"thread_id": "t", "checkpoint_ns": "memory"}}
		).checkpoint["id"]
		== "m1"
	)
