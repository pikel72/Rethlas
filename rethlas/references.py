from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class ReferencePreparation:
    reference_dir: Path
    exists: bool
    extracted_files: List[Path] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def prompt_suffix(self) -> str:
        rel = self.reference_dir.as_posix()
        if self.extracted_files:
            return (
                f"Use reference_dir={rel} if it exists. PDF references have been extracted "
                f"to {rel}/.extracted; read those extracted .txt files instead of the PDFs."
            )
        return f"Use reference_dir={rel} if it exists."


def prepare_references(
    reference_dir: Path,
    generation_dir: Path,
    *,
    extract_pdfs: bool = True,
) -> ReferencePreparation:
    rel_reference_dir = reference_dir.relative_to(generation_dir).as_posix()
    if not reference_dir.is_dir():
        return ReferencePreparation(reference_dir=Path(rel_reference_dir), exists=False)

    pdfs = [
        path
        for path in reference_dir.rglob("*.pdf")
        if ".extracted" not in path.relative_to(reference_dir).parts
    ]
    if not pdfs:
        return ReferencePreparation(reference_dir=Path(rel_reference_dir), exists=True)
    if not extract_pdfs:
        return ReferencePreparation(
            reference_dir=Path(rel_reference_dir),
            exists=True,
            warnings=[f"Found {len(pdfs)} PDF reference(s); extraction skipped for dry planning."],
        )

    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return ReferencePreparation(
            reference_dir=Path(rel_reference_dir),
            exists=True,
            warnings=["Found PDF references, but pdftotext is not installed; PDFs will be ignored."],
        )

    extracted: List[Path] = []
    warnings: List[str] = []
    for pdf in pdfs:
        rel_pdf = pdf.relative_to(reference_dir)
        target = reference_dir / ".extracted" / rel_pdf.with_suffix(".txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_mtime >= pdf.stat().st_mtime:
            extracted.append(target)
            continue
        completed = subprocess.run(
            [pdftotext, "-layout", str(pdf), str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            extracted.append(target)
        else:
            warnings.append(f"Failed to extract {pdf}: {completed.stderr.strip()}")

    return ReferencePreparation(
        reference_dir=Path(rel_reference_dir),
        exists=True,
        extracted_files=extracted,
        warnings=warnings,
    )
