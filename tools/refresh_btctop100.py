#!/usr/bin/env python3
"""Daily refresh of the Corporate Bitcoin Treasury Holdings snapshot.

Reads primary filings for the largest names and rewrites btctop100.json.
The chart reads that file; it does not call this script. Once a day is enough:

  python3 tools/refresh_btctop100.py            # refresh and write
  python3 tools/refresh_btctop100.py --dry-run  # print what would change
  python3 tools/refresh_btctop100.py --fresh    # ignore the document cache

No API keys. SEC requests send a contact User-Agent and stay under 10/second.
bitcointreasuries.net is not contacted.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import btctop100

def main(argv):
    fresh = "--fresh" in argv
    dry = "--dry-run" in argv
    db = btctop100.refresh(fresh=fresh, write=not dry)
    print(btctop100.report(db))
    if dry:
        print("(dry run — btctop100.json not written)")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
