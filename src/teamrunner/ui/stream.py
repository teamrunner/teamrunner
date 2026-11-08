"""Server-Sent Events tail the runner's append-only log; reconnects resume by seq."""

import json

from .api import inside


def events(handler, job_id, after=0):
    jobs = handler.server.jobs
    job = jobs.get(job_id)  # Validate before sending streaming headers.
    handler.send_response(200)
    for name, value in {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                        "X-Accel-Buffering": "no", "Connection": "close"}.items():
        handler.send_header(name, value)
    handler.end_headers()
    stream, pending, last_state, warned = None, b"", None, False

    def send(kind, value, sequence=None):
        frame = (f"id: {sequence}\n" if sequence is not None else "")
        frame += f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
        handler.wfile.write(frame.encode("utf-8"))
        handler.wfile.flush()

    try:
        while True:
            state = jobs.status(job_id)
            encoded = json.dumps(state, sort_keys=True)
            if encoded != last_state:
                send("status", state)
                last_state = encoded
            if stream is None and job.folder:
                path = inside(handler.server.root, (job.folder / "events.jsonl").relative_to(handler.server.root).as_posix())
                if path.parent != job.folder:
                    raise ValueError("The live log points outside its run folder.")
                if path.is_file():
                    stream = path.open("rb")
            data = stream.read(65536) if stream else b""
            pending += data
            lines = pending.split(b"\n")
            pending = lines.pop()
            if state["done"] and not data and pending:
                lines.append(pending)
                pending = b""
            for line in lines:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    sequence = event.get("seq", 0)
                    if not isinstance(sequence, int):
                        raise ValueError()
                except (ValueError, AttributeError):
                    if not warned:
                        send("log-warning", {"message": "An unreadable event was skipped. Raw events remain in the run folder."})
                        warned = True
                    continue
                if sequence > after:
                    send("log", event, sequence)
                    after = sequence
            if data:
                continue  # Drain every flushed event before publishing completion.
            if state["done"]:
                send("done", state)
                return
            handler.wfile.write(b": waiting\n\n")
            handler.wfile.flush()
            job.done.wait(0.15)
    except (BrokenPipeError, ConnectionResetError):
        pass  # Closing a tab does not stop the run.
    except (OSError, ValueError, RuntimeError):
        try:
            send("log-warning", {"message": "The live log could not be read. Reconnect or browse the saved run folder."})
        except (BrokenPipeError, ConnectionResetError):
            pass
    finally:
        if stream:
            stream.close()
