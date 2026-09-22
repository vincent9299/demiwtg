"""Identical canonical model context through API or isolated-agent transport."""
import argparse
import base64
import json
from pathlib import Path


from curation.preparation.records import digest, immutable, read, run_lock, run_records, read_record


def materialize(request_path, output):
    """Only transport changes: data URLs become hash-verified local image links."""
    request = read_record(request_path)
    if digest(request["messages"]) != request["input_sha256"]:
        raise ValueError("Canonical request hash mismatch")
    output = Path(output).resolve()
    images = iter(request["image_roles"])
    blocks, projection = [], []
    for message in request["messages"]:
        blocks.append("# MESSAGE ROLE: " + message["role"] + "\n")
        parts = message["content"]
        if isinstance(parts, str):
            parts = [{"type": "text", "text": parts}]
        for part in parts:
            if part["type"] == "text":
                blocks.append(part["text"] + "\n")
                projection.append({"role": message["role"], "type": "text", "text": part["text"]})
            elif part["type"] == "image_url":
                binding = next(images)
                data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)
                from curation.preparation.materials import asset_pixels
                if digest(data) != binding['sha256'] or asset_pixels(binding) != data:
                    raise ValueError("Portable pixels differ from the actual model request")
                path = output / ('image_' + binding['sha256'] + '.png')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)  # Disposable review export, not authoritative storage.
                blocks.append(f"![{binding['role']}]({path})\n\nImage role: {binding['role']}; sha256: {binding['sha256']}\n")
                projection.append({"role": message["role"], "type": "image", "sha256": binding["sha256"]})
            else:
                raise ValueError("Unsupported message part")
    if next(images, None) is not None:
        raise ValueError("Unused image role binding")
    with run_lock(output):
        immutable(output / "request.json", request)
        immutable(output / "context.json", {"input_sha256": request["input_sha256"],
            "context_projection_sha256": digest(projection), "projection": projection,
            "images": request["image_roles"], "request_path": request_path})
        prompt = "\n".join(blocks)
        path = output / "prompt.md"
        if path.exists() and path.read_text() != prompt:
            raise ValueError("Existing portable prompt differs")
        path.write_text(prompt)
    return output / "prompt.md"


def ingest(request_path, raw_path, run, model, effort, reviewer_kind="assistant"):
    request = read_record(request_path)
    raw_path = Path(raw_path).resolve()
    result = json.loads(raw_path.read_text())
    if not isinstance(result, dict):
        raise ValueError("Raw response must be one JSON object, without markdown fences")
    if digest(request["messages"]) != request["input_sha256"]:
        raise ValueError("Request changed")
    record = {"input_sha256": request["input_sha256"], "result": result,
              "reviewer": model, "reviewer_kind": reviewer_kind,
              "model": model, "reasoning_effort": effort, "raw_ref": run_records(run).put("offline_raw/" + digest(result), result).to_dict(),
              "raw_sha256": digest(result)}
    from demiflow.operator_llm.lance_journal import submit_response
    from project import resolve_root
    binding = request['native_offline']
    content = result if request.get('response_envelope') == 'result' and set(result) == {'result'} else {'result': result}
    return submit_response(resolve_root(), binding['request_ref'], content,
        model=model, metadata={k:v for k,v in record.items() if k not in {'result','input_sha256'}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--request", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    receive = commands.add_parser("ingest")
    for field in ("request", "raw", "run"):
        receive.add_argument("--" + field, required=True, type=Path)
    receive.add_argument("--model", required=True)
    receive.add_argument("--effort", required=True)
    args = parser.parse_args()
    if args.command == "render":
        print(materialize(read(args.request), args.output))
    else:
        print(ingest(read(args.request), args.raw, args.run, args.model, args.effort))


if __name__ == "__main__":
    main()
