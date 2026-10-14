"""Reproduce the local file and heading checks for this delivery."""
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


FILES = ["README.md", "docs/README.md", "docs/USAGE.md", "docs/CAPABILITIES.md",
         "docs/INFORMATION_SOURCES.md", "docs/VALUATION.md", "docs/KNOWLEDGE.md",
         "docs/ARCHITECTURE.md", "CHANGELOG.md", "docs/prompts/README.md",
         "docs/prompts/v0.4.2-implementation-plan.md", "docs/validation/README.md",
         "docs/validation/v0.4.2/README.md"]


def anchors(path):
    source = path.read_text()
    result = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', source))
    seen = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*$", source, re.M):
        base = re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
        index = seen.get(base, 0)
        seen[base] = index + 1
        result.add(base + ("-" + str(index) if index else ""))
    return result


def main():
    errors, local_only, count = [], set(), 0
    for filename in FILES:
        source = Path(filename)
        for target in re.findall(r'\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)', source.read_text()):
            target = target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("//"):
                continue
            path = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source.resolve()
            count += 1
            if not path.exists():
                errors.append(f"{source}: missing {target}")
                continue
            if parsed.fragment and path.suffix == ".md" and unquote(parsed.fragment) not in anchors(path):
                errors.append(f"{source}: missing anchor {target}")
            relative = str(path.relative_to(Path.cwd()))
            if subprocess.run(["git", "ls-files", "--error-unmatch", relative], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode:
                local_only.add(relative)
    result = {"status": "failed" if errors else "passed", "checked_files": len(FILES),
              "checked_local_links": count, "errors": errors, "currently_untracked_link_targets": sorted(local_only),
              "note": "Host prompt migration is independent local work; do not bundle its target."}
    Path("docs/validation/v0.4.2/documentation-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
