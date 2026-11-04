"""An uploaded tender ZIP: the GeM bid PDF and its attachments (BOQ, spec, ATC) in one archive.

GeM bids are often passed around as one ZIP holding bid.pdf and the documents it links to. The
bid document is the PDF printing a GeM bid number on page 1; every other readable file becomes an
attachment with a role from `config/tender_zip_files.yaml`, the same roles linked files get, so
extraction reads both alike. The archive is never trusted: names lose their folders (no path
escapes), and the number of files and their unpacked size are capped before anything is read.
Nested archives and file types extraction cannot read are skipped and reported.
"""

import io
import re
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import yaml
from pydantic import ValidationError

from app.core.exceptions import InvalidInputError, TenderParseError
from app.schemas.tender_files import ZipFileRules
from app.tender.link_fetcher import office_extension

_ZIP_MAGIC: Final = b"PK\x03\x04"
_PDF_SUFFIX: Final = ".pdf"
_BYTES_PER_MB: Final = 1024 * 1024
# Folders that zip tools add and that never hold tender files.
_JUNK_PARTS: Final = frozenset({"__macosx"})


@dataclass(frozen=True, slots=True)
class ZipLimits:
    """Caps applied before an archive is unpacked."""

    max_files: int
    max_unpacked_mb: int
    readable_suffixes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CompiledZipRules:
    """ZIP file-name rules with their patterns compiled, in file order."""

    default_role: str
    rules: tuple[tuple[re.Pattern[str], str], ...]

    def role_for(self, name: str) -> str:
        """The role of the first rule matching the lower-cased file name, else the default."""
        lowered = name.lower()
        return next((role for p, role in self.rules if p.search(lowered)), self.default_role)


@dataclass(frozen=True, slots=True)
class ZipMember:
    """One file of the archive: its path inside the ZIP, its bare name and its bytes."""

    path: str
    name: str
    content: bytes


@dataclass(frozen=True, slots=True)
class UnpackedTender:
    """The bid document, the attachments with their roles, and what was skipped and why."""

    bid_number: str
    bid: ZipMember
    attachments: list[tuple[ZipMember, str]]
    skipped: list[str]


def is_zip_upload(content: bytes) -> bool:
    """True for a ZIP archive that is not an Office file (.docx/.xlsx are ZIPs too).

    Args:
        content: The uploaded bytes.

    Returns:
        Whether the upload is a tender archive.
    """
    return content.startswith(_ZIP_MAGIC) and office_extension(content) is None


def load_zip_rules(path: Path) -> CompiledZipRules:
    """Read and compile the ZIP file rules.

    Args:
        path: The rules YAML.

    Returns:
        Compiled rules.

    Raises:
        TenderParseError: If the file is unreadable, invalid, or has a bad regex.
    """
    try:
        rules = ZipFileRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        compiled = tuple((re.compile(r.pattern), r.role) for r in rules.rules)
    except (OSError, yaml.YAMLError, ValidationError, re.error) as exc:
        raise TenderParseError(
            f"Invalid ZIP file rules {path}: {exc}", details={"path": str(path)}
        ) from exc
    return CompiledZipRules(default_role=rules.default_role, rules=compiled)


def _open(content: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise InvalidInputError("The uploaded ZIP cannot be opened") from exc


def _check_limits(infos: Sequence[zipfile.ZipInfo], limits: ZipLimits) -> None:
    """Refuse an archive with too many files or too much unpacked data (sizes from its index)."""
    if len(infos) > limits.max_files:
        raise InvalidInputError(
            f"The ZIP holds {len(infos)} files; at most {limits.max_files} are accepted",
            details={"files": len(infos)},
        )
    unpacked = sum(i.file_size for i in infos)
    if unpacked > limits.max_unpacked_mb * _BYTES_PER_MB:
        raise InvalidInputError(
            f"The ZIP unpacks to more than {limits.max_unpacked_mb} MB",
            details={"bytes": unpacked},
        )


def read_members(content: bytes, limits: ZipLimits) -> tuple[list[ZipMember], list[str]]:
    """The readable files of an archive, and the skipped ones with the reason.

    Args:
        content: The ZIP bytes.
        limits: File count, unpacked size and the suffixes extraction can read.

    Returns:
        (members in archive order, skipped entries as "path: reason").

    Raises:
        InvalidInputError: If the archive cannot be opened, is too big, or is encrypted.
    """
    members: list[ZipMember] = []
    skipped: list[str] = []
    with _open(content) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        _check_limits(infos, limits)
        for info in infos:
            path = PurePosixPath(info.filename.replace("\\", "/"))
            name = path.name
            if {p.lower() for p in path.parts} & _JUNK_PARTS or name.startswith("."):
                continue
            if info.flag_bits & 0x1:
                raise InvalidInputError("The uploaded ZIP is password protected")
            if path.suffix.lower() not in limits.readable_suffixes:
                skipped.append(f"{info.filename}: file type not read")
                continue
            members.append(ZipMember(info.filename, name, archive.read(info)))
    return members, skipped


def unpack_tender(
    content: bytes,
    rules: CompiledZipRules,
    limits: ZipLimits,
    bid_number_of: Callable[[bytes], str | None],
) -> UnpackedTender:
    """Find the bid document in an uploaded ZIP and give every other file its role.

    Args:
        content: The ZIP bytes.
        rules: File-name roles for the attachments.
        limits: Archive caps and readable suffixes.
        bid_number_of: The GeM bid number on page 1 of a PDF's bytes, or None.

    Returns:
        The bid PDF (the first, by path, printing a bid number), the attachments and the skips.

    Raises:
        InvalidInputError: If the archive is unusable.
        TenderParseError: If no PDF, or PDFs of different bids, print a bid number.
    """
    members, skipped = read_members(content, limits)
    pdfs = sorted(
        (m for m in members if m.name.lower().endswith(_PDF_SUFFIX)), key=lambda m: m.path
    )
    numbered = [(m, n) for m in pdfs if (n := bid_number_of(m.content)) is not None]
    if not numbered:
        raise TenderParseError(
            "No PDF in the ZIP prints a GeM bid number on page 1",
            details={"files": [m.path for m in members]},
        )
    numbers = sorted({n for _, n in numbered})
    if len(numbers) > 1:
        raise TenderParseError(
            f"The ZIP holds PDFs of different bids: {', '.join(numbers)}",
            details={"bid_numbers": numbers},
        )
    bid, bid_number = numbered[0]
    attachments = [(m, rules.role_for(m.name)) for m in members if m is not bid]
    return UnpackedTender(bid_number, bid, attachments, skipped)
