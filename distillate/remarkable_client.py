"""reMarkable Cloud client wrapping the ddvk/rmapi CLI.

All interactions with the reMarkable Cloud go through the `rmapi` binary,
which handles the sync15 protocol and authentication.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from distillate import config

log = logging.getLogger(__name__)


def _run(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run an rmapi command and return the result."""
    rmapi = shutil.which("rmapi")
    if not rmapi:
        raise RuntimeError(
            "rmapi not found. Install it: "
            "https://github.com/ddvk/rmapi/releases"
        )
    cmd = [rmapi] + args
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"rmapi {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result


def ensure_folders() -> None:
    """Create the workflow folders on reMarkable if they don't exist."""
    # Create parent folder first, then subfolders
    _ensure_folder(config.RM_FOLDER_PAPERS)
    for folder in (
        config.RM_FOLDER_INBOX,
        config.RM_FOLDER_READ,
        config.RM_FOLDER_SAVED,
    ):
        _ensure_folder(folder)


def _ensure_folder(folder: str) -> None:
    """Create a folder on reMarkable if it doesn't exist."""
    parent = "/" + "/".join(folder.split("/")[:-1]) if "/" in folder else "/"
    name = folder.split("/")[-1]
    result = _run(["ls", parent], check=False)
    if result.returncode != 0:
        return
    existing = {
        line.split("\t", 1)[-1].strip()
        for line in result.stdout.splitlines()
        if line.startswith("[d]")
    }
    if name not in existing:
        _run(["mkdir", f"/{folder}"])
        log.info("Created reMarkable folder: /%s", folder)


def list_folder(folder: str) -> List[str]:
    """List document names in a reMarkable folder.

    Returns an empty list if the folder doesn't exist.
    """
    result = _run(["ls", f"/{folder}"], check=False)
    if result.returncode != 0:
        return []
    names = []
    for line in result.stdout.splitlines():
        if line.startswith("[f]"):
            name = line.split("\t", 1)[-1].strip()
            names.append(name)
    return names


def upload_pdf_bytes(pdf_bytes: bytes, folder: str, title: str) -> None:
    """Upload PDF bytes to a reMarkable folder with a given title.

    If a document with the same name already exists, skips the upload.
    """
    sanitized = _sanitize_filename(title)
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / f"{sanitized}.pdf"
        dest.write_bytes(pdf_bytes)
        result = _run(["put", str(dest), f"/{folder}/"], check=False)
        if result.returncode != 0:
            if "entry already exists" in result.stderr:
                log.info("Already on reMarkable, skipping: '%s'", title)
                return
            raise RuntimeError(
                f"rmapi put failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
    log.info("Uploaded '%s' to /%s/", title, folder)


def download_document_bundle_to(folder: str, doc_name: str, output_path: Path) -> bool:
    """Download a raw document bundle (zip) using rmapi get.

    Returns True on success, False on failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        rmapi = shutil.which("rmapi")
        if not rmapi:
            raise RuntimeError("rmapi not found")

        result = subprocess.run(
            [rmapi, "get", f"/{folder}/{doc_name}"],
            capture_output=True, text=True, timeout=120,
            cwd=tmpdir,
        )
        if result.returncode != 0:
            log.warning(
                "Failed to download bundle for '%s': %s",
                doc_name, result.stderr.strip(),
            )
            return False

        # rmapi get produces a .zip or .rmdoc file in the working directory
        zips = list(Path(tmpdir).glob("*.zip")) + list(Path(tmpdir).glob("*.rmdoc"))
        if not zips:
            log.warning("rmapi get produced no bundle for '%s'", doc_name)
            return False

        shutil.move(str(zips[0]), str(output_path))
        log.info("Downloaded document bundle: %s", output_path)
        return True


def download_annotated_pdf_to(folder: str, doc_name: str, output_path: Path) -> bool:
    """Download a document as annotated PDF to a specific path.

    Returns True on success, False on failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Run geta from the temp directory so the output lands there
        rmapi = shutil.which("rmapi")
        if not rmapi:
            raise RuntimeError("rmapi not found")

        result = subprocess.run(
            [rmapi, "geta", f"/{folder}/{doc_name}"],
            capture_output=True, text=True, timeout=120,
            cwd=tmpdir,
        )
        if result.returncode != 0:
            log.warning(
                "Failed to download annotated PDF for '%s': %s",
                doc_name, result.stderr.strip(),
            )
            return False

        # Find the generated PDF in tmpdir
        pdfs = list(Path(tmpdir).glob("*.pdf"))
        if not pdfs:
            log.warning("rmapi geta produced no PDF for '%s'", doc_name)
            return False

        shutil.move(str(pdfs[0]), str(output_path))
        log.info("Downloaded annotated PDF: %s", output_path)
        return True


def stat_document(folder: str, doc_name: str) -> Optional[Dict[str, Any]]:
    """Get document metadata from reMarkable. Returns None on failure."""
    result = _run(["stat", f"/{folder}/{doc_name}"], check=False)
    if result.returncode != 0:
        return None
    info = {}
    for line in result.stdout.splitlines():
        if "ModifiedClient:" in line:
            info["modified_client"] = line.split(":", 1)[1].strip()
        if "CurrentPage:" in line:
            try:
                info["current_page"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        if "PageCount:" in line:
            try:
                info["page_count"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return info if info else None


def move_document(doc_name: str, from_folder: str, to_folder: str) -> None:
    """Move a document between reMarkable folders."""
    _run(["mv", f"/{from_folder}/{doc_name}", f"/{to_folder}/"])
    log.info("Moved '%s' from /%s/ to /%s/", doc_name, from_folder, to_folder)


def _sanitize_filename(name: str) -> str:
    """Remove characters that are problematic in filenames."""
    bad_chars = '<>:"/\\|?*'
    result = name
    for c in bad_chars:
        result = result.replace(c, "")
    # Collapse whitespace
    result = " ".join(result.split())
    # Trim to reasonable length
    return result[:200].strip()
