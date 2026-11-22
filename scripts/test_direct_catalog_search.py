#!/usr/bin/env python3
"""Optional NAS network check. Not part of the deterministic CI suite."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.enrichment import fetch_direct

def main() -> int:
    if os.getenv("RUN_NETWORK_TESTS") != "1":
        print("Network test skipped; set RUN_NETWORK_TESTS=1 to run it.")
        return 0
    refs = json.loads((Path(__file__).parents[1] / "examples/references-reelles.json").read_text())
    success = 0
    for ref in refs:
        try:
            result = fetch_direct(ref["code"], ref["designation"])
            ok = result["status"] in {"verified", "review"} and "/fiche/" in result.get("source_url", "")
            success += int(ok)
            print(ref["code"], result["status"], result.get("source_url", ""))
        except Exception as exc:
            print(ref["code"], "ERROR", exc)
        time.sleep(float(os.getenv("ENRICHMENT_DELAY_SECONDS", "2.5")))
    print(f"Result: {success}/{len(refs)} product pages found")
    return 0 if success == len(refs) else 1

if __name__ == "__main__":
    raise SystemExit(main())
