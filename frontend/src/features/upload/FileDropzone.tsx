import { useId, useRef, useState } from "react";
import type { DragEvent } from "react";

import { Button } from "../../components/ui/Button";
import { CloseIcon, FileIcon, UploadIcon } from "../../components/ui/icons";
import { ACCEPTED_TENDER_TYPES, MAX_UPLOAD_MB } from "../../lib/config";
import { formatBytes, validateTenderFile } from "../../lib/files";

interface FileDropzoneProps {
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
}

/** Drag-and-drop area plus a "Browse files" button for one tender PDF or ZIP. */
export function FileDropzone({ file, onChange, disabled = false }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function pick(candidate: File | undefined) {
    if (candidate === undefined) return;
    const problem = validateTenderFile(candidate);
    setError(problem);
    onChange(problem === null ? candidate : null);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!disabled) pick(event.dataTransfer.files[0]);
  }

  if (file !== null) {
    return (
      <div className="flex items-center gap-4 rounded-lg border border-line bg-canvas p-4">
        <span className="flex size-11 items-center justify-center rounded-lg bg-brand-50 text-xl text-brand-600">
          <FileIcon />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{file.name}</p>
          <p className="text-sm text-ink-600">{formatBytes(file.size)}</p>
        </div>
        <Button
          variant="ghost"
          icon={<CloseIcon />}
          onClick={() => {
            onChange(null);
          }}
          disabled={disabled}
          aria-label={`Remove ${file.name}`}
        />
      </div>
    );
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => {
          setDragging(false);
        }}
        onDrop={onDrop}
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragging ? "border-brand-500 bg-brand-50" : "border-line bg-canvas"
        }`}
      >
        <span className="flex size-14 items-center justify-center rounded-full bg-white text-2xl text-brand-600 shadow-card">
          <UploadIcon />
        </span>
        <p className="font-medium">Drag and drop the tender PDF or ZIP here</p>
        <p className="text-sm text-ink-600">
          GeM bid document (bid.pdf), or a ZIP of it with its BOQ, spec and ATC files, up to{" "}
          {MAX_UPLOAD_MB} MB
        </p>
        <label htmlFor={inputId} className="sr-only">
          Tender PDF or ZIP
        </label>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={[...new Set(Object.values(ACCEPTED_TENDER_TYPES))].join(",")}
          className="sr-only"
          disabled={disabled}
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <Button
          variant="secondary"
          disabled={disabled}
          onClick={() => {
            inputRef.current?.click();
          }}
        >
          Browse files
        </Button>
      </div>
      {error !== null && (
        <p role="alert" className="mt-2 text-sm text-status-missing">
          {error}
        </p>
      )}
    </div>
  );
}
