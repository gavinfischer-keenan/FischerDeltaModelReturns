"""Fetch EDI package edi.1479.2 (CDFW Adult Sturgeon Study) metadata and data tables.

Usage:  python -I fetch_edi.py <data_dir>

- EML metadata comes from the DataONE Coordinating Node replica (public).
- Data entities are tried at PASTA, then at EDI's DataONE member node (the location
  DataONE resolves them to). As of 2026-11-26 both answer 403 to anonymous requests and
  the EDI portal sits behind a Cloudflare human-verification check, so the script then
  prints manual-download instructions. It never tries to get around that check.
- Every file present in <data_dir> is verified against the SHA-1 checksum in DataONE
  system metadata, so a manual download is checked too.
"""
import hashlib, re, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

UA = {"User-Agent": "fdmr-runner/0.1"}
PKG = "edi/1479/2"
EML_ID = f"https://pasta.lternet.edu/package/metadata/eml/{PKG}"
ENTITIES = {  # entity hash -> objectName (from the EML)
    "2415693a0756b4c69e2460ea986e3093": "Netset_CDFW_Sturgeon_Study.csv",
    "c8bd39be800bf17863111146e8c38f17": "Catch_CDFW_Sturgeon_Study1.csv",
    "6f4fef1f052e535cf02e48ecc2082e5a": "TagReturn_CDFW_Sturgeon_Study.csv",
    "b12f76bfd363521995af4ab5bd179c7b": "Sturgeon_Metadata_073123.docx",
}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def sysmeta_sha1(pid):
    xml = get("https://cn.dataone.org/cn/v2/meta/" + urllib.parse.quote(pid, safe="")).decode()
    m = re.search(r'<checksum algorithm="SHA-1">([0-9a-f]+)<', xml)
    return m.group(1) if m else None


def main(d):
    d = Path(d); d.mkdir(parents=True, exist_ok=True)
    eml = d / "edi.1479.2.eml.xml"
    if not eml.exists():
        eml.write_bytes(get("https://cn.dataone.org/cn/v2/object/" + urllib.parse.quote(EML_ID, safe="")))
    print("EML", eml, "sha1 ok:", hashlib.sha1(eml.read_bytes()).hexdigest() == sysmeta_sha1(EML_ID))
    missing = []
    for h, name in ENTITIES.items():
        pid = f"https://pasta.lternet.edu/package/data/eml/{PKG}/{h}"
        want = sysmeta_sha1(pid)
        f = d / name
        if not f.exists():
            for url in (pid, "https://gmn.edirepository.org/mn/v2/object/" + urllib.parse.quote(pid, safe="")):
                try:
                    f.write_bytes(get(url)); print("downloaded", name, "from", url); break
                except urllib.error.HTTPError as e:
                    print(f"  {name}: {url} -> HTTP {e.code}")
        if f.exists():
            got = hashlib.sha1(f.read_bytes()).hexdigest()
            print(f"{name}: sha1 {'OK' if got == want else 'MISMATCH'} (expected {want}, got {got})")
            if got != want:
                f.unlink(); missing.append(name)
        else:
            missing.append(name)
    if missing:
        print("\nNot downloadable anonymously. Download these by hand from\n"
              "  https://portal.edirepository.org/nis/mapbrowse?packageid=edi.1479.2\n"
              f"(pass the portal's human check yourself) and save them, unrenamed, into {d}:")
        for m in missing:
            print("  -", m)
        print("Then rerun this script to verify checksums.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
