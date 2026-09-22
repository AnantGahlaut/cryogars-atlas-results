"""Copy the nine public viewer pages, removing local paths from lineage only.

No archive is opened. Scientific JSON and executable HTML must stay identical.
The destination must be empty; the local viewer and its full lineage stay intact.
"""
import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


SITES = ("banner_summit", "cameron_pass", "dry_creek", "fraser", "grand_mesa",
         "little_cottonwood", "mores_creek", "reynolds_creek")
PAGES = ("index.html",) + tuple(site + "_explorer.html" for site in SITES)
SCRIPT = re.compile(r'(<script\b[^>]*>)([\s\S]*?)(</script>)', re.IGNORECASE)
LOCAL_PATH = re.compile(r'^(?:[A-Za-z]:[/\\]|\\\\|/(?:home|Users|mnt|tmp|opt)/|~/|file:)', re.I)
LOCAL_IN_TEXT = re.compile(r'(?<![\w:])(?:[A-Za-z]:[\\/]|/(?:home|Users)/|file:///)', re.I)
LINEAGE_KEYS = {"build_provenance", "metadata_correction_provenance"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def redact_path(value, repository):
    if not LOCAL_PATH.match(value):
        return value
    normalized = value.replace("\\", "/")
    root = str(repository).replace("\\", "/").rstrip("/") + "/"
    if normalized.lower().startswith(root.lower()):
        return "repository/" + normalized[len(root):]
    return "[local]/" + normalized.rstrip("/").rsplit("/", 1)[-1]


def redact_lineage(value, repository):
    if isinstance(value, str):
        return redact_path(value, repository)
    if isinstance(value, list):
        return [redact_lineage(item, repository) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        public_key = redact_path(key, repository)
        if public_key in result:
            raise ValueError("Redacted source path collision: " + public_key)
        result[public_key] = redact_lineage(item, repository)
    if result != value and value.get("schema") == "snowex-build-lineage-v1":
        result["pre_redaction_record_sha256"] = digest(canonical(value))
        if "recipe_sha256" in result:
            result["pre_redaction_recipe_sha256"] = result.pop("recipe_sha256")
        result["recipe_status"] = "redacted_recipe_not_checksum_verifiable"
        result["publication_redaction"] = (
            "Local absolute paths removed; original record and recipe digests are "
            "pre-redaction identities. Individual source-file byte hashes are retained.")
    return result


def public_page(raw, repository):
    """Replace JSON bodies only; fail on unhandled local paths elsewhere."""
    text = raw.decode("utf-8")
    changed = []

    def replace(match):
        opening, body, closing = match.groups()
        if not re.search(r'type=[\"\']application/json[\"\']', opening, re.I):
            return match.group(0)
        value = json.loads(body)
        identifier = re.search(r'id=[\"\']([^\"\']+)', opening)
        identifier = identifier.group(1) if identifier else ""
        if identifier == "render-provenance":
            public = redact_lineage(value, repository)
        elif identifier == "payload":
            public = {key: redact_lineage(item, repository) if key in LINEAGE_KEYS else item
                      for key, item in value.items()}
            assert {k: v for k, v in public.items() if k not in LINEAGE_KEYS} == {
                k: v for k, v in value.items() if k not in LINEAGE_KEYS}
        else:
            public = value
        if public == value:
            return match.group(0)
        changed.append(identifier)
        # Escape closing-tag text to keep arbitrary JSON out of HTML parsing.
        return opening + json.dumps(public, ensure_ascii=True, allow_nan=False).replace("</", "<\\/") + closing

    public = SCRIPT.sub(replace, text)
    if LOCAL_IN_TEXT.search(public):
        raise ValueError("Unhandled local path outside publication lineage")
    # Removing JSON bodies must leave every executable script and HTML byte intact.
    def without_json(match):
        return match.group(1) + match.group(3) if "application/json" in match.group(1) else match.group(0)
    assert SCRIPT.sub(without_json, text) == SCRIPT.sub(without_json, public)
    return public.encode("utf-8"), changed


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.urls.add(value)


def check_links(raw):
    parser = Links()
    parser.feed(raw.decode("utf-8"))
    external = []
    for url in sorted(parser.urls):
        parsed = urlsplit(url)
        if parsed.scheme in {"https", "http"}:
            external.append(url)
        elif parsed.scheme == "data" or url.startswith("#"):
            continue
        elif parsed.path not in PAGES or parsed.scheme or parsed.netloc:
            raise ValueError("Unbundled page dependency: " + url)
    return external


def package(source, destination, repository):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Public copies must be outside the source viewer directory")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Public destination must be empty")
    report = {"status": "passed", "scope": "nine viewer HTML pages only",
              "scientific_payload_and_executable_html": "unchanged", "pages": []}
    for name in PAGES:
        path = source / name
        raw = path.read_bytes()
        public, changed = public_page(raw, repository)
        external = check_links(public)
        (destination / name).write_bytes(public)
        if digest(path.read_bytes()) != digest(raw):
            raise ValueError("Source changed during packaging: " + name)
        report["pages"].append({"name": name, "source_sha256": digest(raw),
                                "public_sha256": digest(public), "bytes": len(public),
                                "redacted_json_blocks": changed, "external_links": external})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = package(args.source, args.destination, args.repository)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "pages": len(result["pages"]),
                      "bytes": sum(page["bytes"] for page in result["pages"])}))
