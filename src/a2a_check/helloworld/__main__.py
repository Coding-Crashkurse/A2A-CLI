# src/a2a_check/helloworld/__main__.py
# FastAPI A2A Long-Task Demo-Server mit parametrisierbarer AgentCard
# Läuft rein über HTTP+JSON (REST) mit SSE, Storage/Broker/Worker.
# Modus-abhängige AgentCard: ok | warn | mixed | errors

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import random
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

from fastapi import Body, FastAPI, APIRouter, Response, Request
from fastapi.responses import JSONResponse, StreamingResponse

# A2A-Pydantic-Typen
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    UnsupportedOperationError,
    # zusätzliche Part-Varianten
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
)

# ------------------------------------------------------------
# Globale Modus-Konfiguration (wird in main() gesetzt)
# ------------------------------------------------------------
MODE: Literal["ok", "errors", "mixed", "warn"] = "ok"

# Terminale Zustände
TERMINAL_STATES = {
    TaskState.completed,
    TaskState.failed,
    TaskState.canceled,
    TaskState.rejected,
}

# ======================================================================
#                              Storage
# ======================================================================
class InMemoryStorage:
    """Einfacher In‑Memory‑Store mit serverseitigen Timestamps für Store und Task."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._contexts: dict[str, list[Message]] = {}
        self._lock = asyncio.Lock()
        # Serverseitige Timestamps für „Store“
        self._store_created_at: str = self._now()
        self._store_updated_at: str = self._store_created_at
        # pro Task: {"created_at": str, "updated_at": str}
        self._task_ts: dict[str, dict[str, str]] = {}

    @staticmethod
    def _now() -> str:
        # RFC3339 / UTC
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _touch_store(self) -> None:
        self._store_updated_at = self._now()

    def _touch_task(self, tid: str) -> dict[str, str]:
        now = self._now()
        ts = self._task_ts.get(tid)
        if ts is None:
            ts = {"created_at": now, "updated_at": now}
            self._task_ts[tid] = ts
        else:
            ts["updated_at"] = now
        return ts

    def _apply_ts_metadata(self, task: Task) -> None:
        """Schreibt serverseitige Zeitstempel in task.metadata.timestamps.
        Falls status.timestamp fehlt, befüllen wir ihn mit task.updatedAt."""
        ts = self._task_ts.get(task.id) or self._touch_task(task.id)
        meta = task.metadata or {}
        meta["timestamps"] = {
            "task": {"createdAt": ts["created_at"], "updatedAt": ts["updated_at"]},
            "store": {"createdAt": self._store_created_at, "updatedAt": self._store_updated_at},
        }
        task.metadata = meta
        if not getattr(task.status, "timestamp", None):
            task.status.timestamp = ts["updated_at"]

    # Sync-Getter (für Header-Erzeugung ohne Locks)
    def get_task_timestamps(self, tid: str) -> dict[str, str]:
        return dict(self._task_ts.get(tid, {}))

    @property
    def store_created_at(self) -> str:
        return self._store_created_at

    @property
    def store_updated_at(self) -> str:
        return self._store_updated_at

    async def create_task(self, params: MessageSendParams) -> Task:
        async with self._lock:
            tid = str(uuid.uuid4())
            cid = str(uuid.uuid4())
            msg = params.message
            msg.context_id = cid
            msg.task_id = tid
            task = Task(
                id=tid,
                context_id=cid,
                status=TaskStatus(state=TaskState.submitted, timestamp=self._now()),
                history=[msg],
            )
            self._tasks[tid] = task
            self._contexts.setdefault(cid, []).append(msg)
            self._touch_task(tid)
            self._touch_store()
            self._apply_ts_metadata(task)
            return task

    async def load_task(self, task_id: str) -> Task | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self) -> list[Task]:
        async with self._lock:
            return list(self._tasks.values())

    async def update_task(
        self,
        task_id: str,
        *,
        state: TaskState | None = None,
        status_message_text: str | None = None,
        new_messages: list[Message] | None = None,
        new_artifacts: list[Artifact] | None = None,
    ) -> Task:
        async with self._lock:
            task = self._tasks[task_id]
            if state is not None:
                task.status.state = state
            if status_message_text is not None:
                task.status.message = Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=status_message_text))],
                    kind="message",
                    message_id=str(uuid.uuid4()),
                    context_id=task.context_id,
                    task_id=task.id,
                )
            else:
                task.status.message = None
            if new_messages:
                self._contexts[task.context_id].extend(new_messages)
                task.history = (task.history or []) + new_messages
            if new_artifacts is not None:
                task.artifacts = new_artifacts
            # Status- und Store-/Task-Timestamps aktualisieren
            task.status.timestamp = self._now()
            self._touch_task(task_id)
            self._touch_store()
            self._apply_ts_metadata(task)
            return task


# ======================================================================
#                              EventBus
# ======================================================================
class EventBus:
    """SSE‑Pub/Sub je Task."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subs[task_id].add(q)
        return q

    async def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs[task_id].discard(q)
            if not self._subs[task_id]:
                del self._subs[task_id]

    async def publish(self, task_id: str, event: Any) -> None:
        async with self._lock:
            queues = list(self._subs.get(task_id, []))
        for q in queues:
            await q.put(event)


# ======================================================================
#                                Broker
# ======================================================================
class InMemoryBroker:
    """Einfacher Broker, der Jobs an den Worker gibt."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    async def enqueue(self, job: dict[str, Any]) -> None:
        await self._queue.put(job)

    async def run(self, worker: "Worker") -> None:
        if self._running:
            return
        self._running = True

        async def loop() -> None:
            while self._running:
                job = await self._queue.get()
                asyncio.create_task(worker.run_task(job))

        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


# ======================================================================
#                                Worker
# ======================================================================
class Worker:
    """Simulierter Long‑Task mit optionalen Zwischen-Events und Artefakten."""

    def __init__(self, storage: InMemoryStorage, broker: InMemoryBroker, bus: EventBus) -> None:
        self.storage = storage
        self.broker = broker
        self.bus = bus
        self._cancel_flags: dict[str, asyncio.Event] = {}

    # --- Helpers / Randomisierung ---
    @staticmethod
    def _maybe(p: float) -> bool:
        return random.random() < p

    @staticmethod
    def _b64(s: str) -> str:
        return base64.b64encode(s.encode("utf-8")).decode("ascii")

    def _rand_metadata(self) -> dict[str, Any] | None:
        if not self._maybe(0.5):
            return None
        return {
            "uiHint": random.choice(["primary", "secondary", "warning"]),
            "tags": random.sample(["demo", "report", "log", "json", "file"], k=random.randint(1, 3)),
        }

    def _text_part(self, text: str) -> Part:
        return Part(root=TextPart(text=text, metadata=self._rand_metadata()))

    def _file_bytes_part(self, name: str, mime: str, content: str) -> Part:
        return Part(root=FilePart(file=FileWithBytes(name=name, mime_type=mime, bytes=self._b64(content))))

    def _file_uri_part(self, name: str, mime: str, uri: str) -> Part:
        return Part(root=FilePart(file=FileWithUri(name=name, mime_type=mime, uri=uri)))

    def _data_part(self, data: dict[str, Any]) -> Part:
        return Part(root=DataPart(data=data, metadata=self._rand_metadata()))

    def _make_extra_history(self, context_id: str, task_id: str) -> list[Message]:
        msgs: list[Message] = []
        if self._maybe(0.7):
            msgs.append(
                Message(
                    role=Role.agent,
                    parts=[self._text_part("Okay, ich starte mit der Verarbeitung.")],
                    kind="message",
                    message_id=str(uuid.uuid4()),
                    context_id=context_id,
                    task_id=task_id,
                    metadata=self._rand_metadata(),
                    extensions=(["urn:example:demo/extensions"] if self._maybe(0.4) else None),
                )
            )
        if self._maybe(0.6):
            msgs.append(
                Message(
                    role=Role.user,
                    parts=[self._text_part("Klingt gut. Bitte auch eine Kurz-Zusammenfassung anhängen.")],
                    kind="message",
                    message_id=str(uuid.uuid4()),
                    context_id=context_id,
                    task_id=task_id,
                    metadata=self._rand_metadata(),
                )
            )
        if self._maybe(0.5):
            msgs.append(
                Message(
                    role=Role.agent,
                    parts=[self._text_part("Zwischenstand: Parsing abgeschlossen."), self._data_part({"progress": random.randint(20, 60), "unit": "%"})],
                    kind="message",
                    message_id=str(uuid.uuid4()),
                    context_id=context_id,
                    task_id=task_id,
                )
            )
        return msgs

    def _make_artifacts(self, task_id: str) -> list[Artifact]:
        arts: list[Artifact] = []

        # 1) result-Text immer dabei
        arts.append(
            Artifact(
                artifact_id=f"artifact-{uuid.uuid4().hex}",
                name="result",
                description=("Zusammenfassung des Ergebnisses" if self._maybe(0.5) else None),
                parts=[self._text_part(random.choice(["Success: Task abgeschlossen.", "Fertig. Ergebnis liegt bei.", "Erledigt. Siehe weitere Artefakte."]))],
                extensions=(["urn:example:demo/extensions"] if self._maybe(0.3) else None),
            )
        )

        # 2) Optional JSON-Report
        if self._maybe(0.7):
            arts.append(
                Artifact(
                    artifact_id=f"artifact-{uuid.uuid4().hex}",
                    name="report.json",
                    description="Strukturierte Auswertung",
                    parts=[self._data_part({"taskId": task_id, "score": round(random.uniform(0.5, 0.99), 2), "items": [{"id": i, "ok": self._maybe(0.8)} for i in range(random.randint(2, 5))]})],
                )
            )

        # 3) Optional Log-Datei als Bytes
        if self._maybe(0.6):
            log = "\n".join(
                [
                    "=== demo.log ===",
                    f"task={task_id}",
                    f"ts={datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}",
                    "status=ok",
                ]
            )
            arts.append(
                Artifact(
                    artifact_id=f"artifact-{uuid.uuid4().hex}",
                    name="demo.log.txt",
                    description="Roh-Log",
                    parts=[self._file_bytes_part("demo.log.txt", "text/plain", log)],
                )
            )

        # 4) Optional Vorschaubild als URI
        if self._maybe(0.4):
            arts.append(
                Artifact(
                    artifact_id=f"artifact-{uuid.uuid4().hex}",
                    name="preview.png",
                    description="Beispielbild (URI)",
                    parts=[self._file_uri_part("preview.png", "image/png", f"https://files.example.com/a2a/{task_id}/preview.png")],
                )
            )

        return arts

    async def run(self) -> None:
        await self.broker.run(self)

    async def run_task(self, job: dict[str, Any]) -> None:
        task_id: str = job["task_id"]
        duration: int = job.get("duration", 60)  # exakt 60s
        self._cancel_flags.setdefault(task_id, asyncio.Event())

        def cancelled() -> bool:
            return self._cancel_flags[task_id].is_set()

        # Start -> working
        task = await self.storage.update_task(task_id, state=TaskState.working, status_message_text="Task gestartet.")
        await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=False))

        # gleich zu Beginn optionale Extra-History
        extra_msgs = self._make_extra_history(task.context_id, task.id)
        if extra_msgs:
            task = await self.storage.update_task(task_id, new_messages=extra_msgs)
            await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=False))

        # Fortschritt alle 5s über 60s
        steps = max(1, duration // 5)  # 12 Schritte
        mid_injected = False
        for i in range(1, steps + 1):
            if cancelled():
                current = await self.storage.load_task(task_id)
                if current and current.status.state != TaskState.canceled:
                    current = await self.storage.update_task(task_id, state=TaskState.canceled, status_message_text="Abgebrochen.")
                    await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=current.id, context_id=current.context_id, status=current.status, final=True))
                return

            await asyncio.sleep(5)
            pct = int(i * 100 / steps)

            if not mid_injected and pct >= 50 and self._maybe(0.7):
                mid_injected = True
                mid_msg = Message(
                    role=Role.agent,
                    parts=[self._text_part(f"Zwischenstand: {pct}%"), self._data_part({"progress": pct})],
                    kind="message",
                    message_id=str(uuid.uuid4()),
                    context_id=task.context_id,
                    task_id=task.id,
                )
                task = await self.storage.update_task(task_id, state=TaskState.working, status_message_text=f"Progress: {pct}%", new_messages=[mid_msg])
            else:
                task = await self.storage.update_task(task_id, state=TaskState.working, status_message_text=f"Progress: {pct}%")

            await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=False))

        # Ergebnisverteilung: 80% completed, 10% input-required, 10% failed
        r = random.random()
        task = await self.storage.load_task(task_id)
        assert task

        if r < 0.8:
            artifacts = self._make_artifacts(task.id)
            for idx, art in enumerate(artifacts):
                await self.bus.publish(task_id, TaskArtifactUpdateEvent(task_id=task.id, context_id=task.context_id, artifact=art, last_chunk=(idx == len(artifacts) - 1), append=False))
            task = await self.storage.update_task(task_id, state=TaskState.completed, new_artifacts=artifacts, status_message_text=random.choice(["Fertig.", "Done.", "Abschluss erreicht."]))
            await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=True))
        elif r < 0.9:
            task = await self.storage.update_task(task_id, state=TaskState.input_required, status_message_text="Weitere Eingabe erforderlich: Bitte Parameter spezifizieren.")
            await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=True))
        else:
            task = await self.storage.update_task(task_id, state=TaskState.failed, status_message_text="Fehler aufgetreten.")
            await self.bus.publish(task_id, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=True))

    async def cancel_task(self, params: TaskIdParams) -> Task:
        """Hard-cancel: setzt Flag, aktualisiert Status und sendet finales SSE."""
        tid = params.id
        self._cancel_flags.setdefault(tid, asyncio.Event()).set()
        task = await self.storage.load_task(tid)
        if not task:
            raise KeyError("Task not found")

        if task.status.state in TERMINAL_STATES:
            return task

        task = await self.storage.update_task(tid, state=TaskState.canceled, status_message_text="Abgebrochen.")
        await self.bus.publish(tid, TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=True))
        return task


# ======================================================================
#                             TaskManager
# ======================================================================
class TaskManager:
    def __init__(self, storage: InMemoryStorage, broker: InMemoryBroker, worker: Worker, bus: EventBus) -> None:
        self.storage = storage
        self.broker = broker
        self.worker = worker
        self.bus = bus

    async def send(self, params: MessageSendParams) -> Task:
        task = await self.storage.create_task(params)
        await self.broker.enqueue({"task_id": task.id, "duration": 60})
        return task

    async def stream(self, params: MessageSendParams) -> tuple[Task, asyncio.Queue]:
        task = await self.storage.create_task(params)
        q = await self.bus.subscribe(task.id)
        await self.broker.enqueue({"task_id": task.id, "duration": 60})
        return task, q

    async def resubscribe(self, task_id: str) -> tuple[Task, asyncio.Queue]:
        task = await self.storage.load_task(task_id)
        if not task:
            raise KeyError("Task not found")
        q = await self.bus.subscribe(task_id)
        return task, q

    async def get(self, params: TaskQueryParams) -> Task:
        task = await self.storage.load_task(params.id)
        if not task:
            raise KeyError("Task not found")
        return task

    async def list(self) -> list[Task]:
        return await self.storage.list_tasks()

    async def cancel(self, params: TaskIdParams) -> Task:
        return await self.worker.cancel_task(params)


# ======================================================================
#                        AgentCard-Builder (Modus)
# ======================================================================

def _jd(model_or_dict: Any) -> dict:
    """Wenn Pydantic‑Modell: model_dump; ansonsten dict unverändert."""
    if hasattr(model_or_dict, "model_dump"):
        return model_or_dict.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]
    return dict(model_or_dict)


def _card_ok(base: str) -> dict:
    """Valide Card: REST bevorzugt."""
    ac = AgentCard(
        name="A2A FastAPI LongTask Agent",
        description="FastAPI-Server mit Storage/Broker/Worker. 60s-Task, SSE, 80/10/10 Outcome.",
        # REST-Basis ist die Host-URL; Endpunkte sind /v1/...
        url=base,
        preferred_transport="HTTP+JSON",
        version="0.1.3",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False, state_transition_history=False),
        supports_authenticated_extended_card=True,
        skills=[
            AgentSkill(
                id="long-task-60s-prob",
                name="60s Long Task (80/10/10)",
                description="Läuft 60 Sekunden. 80% completed, 10% input-required, 10% failed.",
                tags=["long-running", "demo"],
                examples=["Starte einen 60s-Task."],
            )
        ],
    )
    d = ac.model_dump(mode="json", exclude_none=True)
    # Für Vollständigkeit (CardChecks mögen das):
    d["additional_interfaces"] = [{"url": base, "transport": "HTTP+JSON"}]
    return d


def _card_warn(base: str) -> dict:
    """Schema‑valide, aber mit Warnungen (SemVer, Icon-URL, alte Protokollversion)."""
    d = _card_ok(base)
    d["protocol_version"] = "0.2.9"  # alt -> WARN
    d["version"] = "1.0"            # nicht semver‑like -> WARN
    d["icon_url"] = "ftp://example.com/icon.png"  # kein http/https -> WARN
    return d


def _card_mixed(base: str) -> dict:
    """Mix aus Fehlern und Warnungen."""
    # Start von warn, dann Fehler einbauen
    d = _card_warn(base)
    # Fehler 1: default_input_modes entfernt -> Schemafehler
    d.pop("default_input_modes", None)
    # Fehler 2: Transport‑Wert absichtlich falsch geschrieben
    d["additional_interfaces"] = [
        {"url": base, "transport": "HTTP_JSON"},  # falscher Wert -> WARN/ERROR
        {"url": base, "transport": "JSONRPC"},    # zweiter Transport für gleiche URL -> Konflikt
    ]
    # Fehler 3: preferred_transport auf JSONRPC (Server kann das hier nicht)
    d["preferred_transport"] = "JSONRPC"
    return d


def _card_errors(base: str) -> dict:
    """Bewusst „kaputte“ Card mit klaren Schemafehlern."""
    return {
        # protocol_version absichtlich als korrekter String, damit CARD-001 präsent ist
        "protocol_version": "0.3.0",
        # name fehlt -> ERROR
        # description vorhanden
        "description": "Broken card for ERROR demo.",
        # url
        "url": base,
        "preferred_transport": "JSONRPC",  # passt nicht zum Server
        "additional_interfaces": [
            {"url": base, "transport": "GRPC"},
            {"url": base, "transport": "JSONRPC"},    # mehrere, aber kein HTTP+JSON
            {"url": base, "transport": "HTTP_JSON"},  # Tippfehler-Wert
        ],
        "version": "1.0",  # nicht semver‑like
        "capabilities": {
            "streaming": "yes",          # falscher Typ
            "push_notifications": False,
        },
        # default_input_modes fehlt komplett -> ERROR
        "default_output_modes": ["application/json", "text/plain"],
        "skills": [
            {"id": "hello_world", "name": "Hello World", "tags": ["demo"]},  # fehlende description
            {"id": "hello_world", "name": "Hello World Again", "description": "dup", "tags": ["x"]},  # dup id
        ],
        "supports_authenticated_extended_card": False,
        "icon_url": "ftp://example.com/icon.png",  # WARN
    }


def _card_for_mode(base: str) -> dict:
    m = (MODE or "ok").strip().lower()
    if m == "errors":
        return _card_errors(base)
    if m == "mixed":
        return _card_mixed(base)
    if m == "warn":
        return _card_warn(base)
    return _card_ok(base)


# ======================================================================
#                              FastAPI
# ======================================================================

storage = InMemoryStorage()
broker = InMemoryBroker()
bus = EventBus()
worker = Worker(storage, broker, bus)
manager = TaskManager(storage, broker, worker, bus)

router = APIRouter(tags=["A2A"])


# -------------------------- REST Endpoints -----------------------------

@router.post(
    "/v1/message:send",
    summary="message/send",
    response_model=Task,
)
async def message_send(
    params: MessageSendParams = Body(
        ...,
        examples={
            "default": {
                "summary": "Einfacher Start",
                "value": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": "go"}],
                        "kind": "message",
                        "messageId": "11111111-1111-1111-1111-111111111111",
                    }
                }
            }
        },
    ),
    response: Response = None,  # Response ist ein injected Param, nicht Teil des Schemas
):
    task = await manager.send(params)
    ts = storage.get_task_timestamps(task.id)
    response.headers["Last-Modified"] = ts.get("updated_at", storage.store_updated_at)
    response.headers["X-Task-Created-At"] = ts.get("created_at", "")
    response.headers["X-Task-Updated-At"] = ts.get("updated_at", "")
    response.headers["X-Store-Updated-At"] = storage.store_updated_at
    return _jd(task)


async def _sse_event_json(model: Any) -> bytes:
    payload = _jd(model)
    payload["_server_ts"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["_store_ts"] = {"created_at": storage.store_created_at, "updated_at": storage.store_updated_at}
    tid = getattr(model, "id", None) or getattr(model, "task_id", None)
    if tid:
        tts = storage.get_task_timestamps(tid)
        if tts:
            payload["_task_ts"] = tts
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


async def _sse_generator(task: Task, q: asyncio.Queue) -> AsyncIterator[bytes]:
    # Snapshot
    yield await _sse_event_json(task)
    try:
        while True:
            ev = await q.get()
            yield await _sse_event_json(ev)
            if isinstance(ev, TaskStatusUpdateEvent) and ev.final:
                break
    finally:
        await bus.unsubscribe(task.id, q)


@router.post(
    "/v1/message:stream",
    summary="message/stream",
    responses={200: {"description": "SSE: Task | status-update | artifact-update"}},
)
async def message_stream(params: MessageSendParams):
    task, q = await manager.stream(params)
    return StreamingResponse(
        _sse_generator(task, q),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/v1/tasks/{id}", summary="tasks/get", response_model=Task)
async def tasks_get(id: str, response: Response):
    try:
        task = await manager.get(TaskQueryParams(id=id))
        storage._apply_ts_metadata(task)
        ts = storage.get_task_timestamps(task.id)
        response.headers["Last-Modified"] = ts.get("updated_at", storage.store_updated_at)
        response.headers["X-Task-Created-At"] = ts.get("created_at", "")
        response.headers["X-Task-Updated-At"] = ts.get("updated_at", "")
        response.headers["X-Store-Updated-At"] = storage.store_updated_at
        return _jd(task)
    except KeyError:
        return JSONResponse({"code": -32001, "message": "Task not found"}, status_code=404)


@router.get("/v1/tasks", summary="tasks/list", response_model=list[Task])
async def tasks_list(response: Response):
    items = await manager.list()
    for t in items:
        storage._apply_ts_metadata(t)
    response.headers["X-Store-Updated-At"] = storage.store_updated_at
    return [_jd(t) for t in items]


async def _resub_generator(task: Task, q: asyncio.Queue) -> AsyncIterator[bytes]:
    # 1) Snapshot: Task
    yield await _sse_event_json(task)
    # 2) Status‑Snapshot als status-update
    snapshot = TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status, final=(task.status.state in TERMINAL_STATES))
    yield await _sse_event_json(snapshot)
    if snapshot.final:
        return
    try:
        while True:
            ev = await q.get()
            yield await _sse_event_json(ev)
            if isinstance(ev, TaskStatusUpdateEvent) and ev.final:
                break
    finally:
        await bus.unsubscribe(task.id, q)


@router.post(
    "/v1/tasks/{id}:subscribe",
    summary="tasks/resubscribe",
    responses={200: {"description": "SSE: weitere Events für bestehenden Task"}},
)
async def tasks_subscribe(id: str):
    try:
        task, q = await manager.resubscribe(id)
        storage._apply_ts_metadata(task)
        return StreamingResponse(
            _resub_generator(task, q),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    except KeyError:
        return JSONResponse({"code": -32001, "message": "Task not found"}, status_code=404)


@router.post("/v1/tasks/{id}:cancel", summary="tasks/cancel", response_model=Task)
async def tasks_cancel(id: str, response: Response):
    try:
        task = await manager.cancel(TaskIdParams(id=id))
        storage._apply_ts_metadata(task)
        ts = storage.get_task_timestamps(task.id)
        response.headers["Last-Modified"] = ts.get("updated_at", storage.store_updated_at)
        response.headers["X-Task-Created-At"] = ts.get("created_at", "")
        response.headers["X-Task-Updated-At"] = ts.get("updated_at", "")
        response.headers["X-Store-Updated-At"] = storage.store_updated_at
        return _jd(task)
    except KeyError:
        return JSONResponse({"code": -32001, "message": "Task not found"}, status_code=404)


# Push-Endpoints: not implemented
@router.post("/v1/tasks/{id}/pushNotificationConfigs", summary="tasks/pushNotificationConfig/set")
async def push_set(id: str):
    return JSONResponse(UnsupportedOperationError().model_dump(mode="json"), status_code=501)


@router.get("/v1/tasks/{id}/pushNotificationConfigs", summary="tasks/pushNotificationConfig/list")
async def push_list(id: str):
    return JSONResponse(UnsupportedOperationError().model_dump(mode="json"), status_code=501)


@router.get("/v1/tasks/{id}/pushNotificationConfigs/{push_id}", summary="tasks/pushNotificationConfig/get")
async def push_get(id: str, push_id: str):
    return JSONResponse(UnsupportedOperationError().model_dump(mode="json"), status_code=501)


@router.delete("/v1/tasks/{id}/pushNotificationConfigs/{push_id}", summary="tasks/pushNotificationConfig/delete")
async def push_delete(id: str, push_id: str):
    return JSONResponse(UnsupportedOperationError().model_dump(mode="json"), status_code=501)


# -------- AgentCard Endpoints --------

@router.get("/.well-known/agent-card.json", summary="Get Agent Card")
async def get_agent_card(request: Request):
    # Basis-URL aus der tatsächlichen Request-Hostheader bestimmen (robust für 0.0.0.0 Bind)
    # Optional per Env überschreibbar
    base = os.getenv("A2A_BASE_URL")
    if not base:
        base = str(request.base_url).rstrip("/")
    return _card_for_mode(base)


@router.get("/v1/card", summary="agent/getAuthenticatedExtendedCard")
async def get_authenticated_card(request: Request):
    base = os.getenv("A2A_BASE_URL") or str(request.base_url).rstrip("/")
    return _card_for_mode(base)


# -------------------------- App / Lifespan -----------------------------

app = FastAPI(title="A2A FastAPI LongTask Server", version="0.1.3")
app.include_router(router)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await worker.run()
    yield
    await broker.stop()


app.router.lifespan_context = lifespan


# ======================================================================
#                               entrypoint
# ======================================================================
def main(host: str = "127.0.0.1", port: int = 9999, mode: str = "ok") -> None:
    """Entry für CLI: a2a-check start_dummy --host ... --port ... --mode ..."""
    import uvicorn

    global MODE
    m = (mode or "ok").strip().lower()
    if m not in ("ok", "errors", "mixed", "warn"):
        m = "ok"
    MODE = m

    os.environ["PORT"] = str(port)

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    p = int(os.getenv("PORT", "8003"))
    main(host="0.0.0.0", port=p, mode=os.getenv("MODE", "ok"))
