import { useState } from "react";
import type { SyntheticEvent } from "react";
import { useNavigate } from "react-router-dom";

import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { FactoryIcon, FileIcon, PlayIcon } from "../../components/ui/icons";
import { StepCard } from "../../components/ui/StepCard";
import { useStartMatching } from "../../hooks/queries";
import { FileDropzone } from "./FileDropzone";
import { ManufacturerPicker } from "./ManufacturerPicker";

/** The whole upload flow: file -> category + manufacturers -> start matching. */
export function UploadForm() {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState("");
  const [manufacturers, setManufacturers] = useState<string[]>([]);
  const navigate = useNavigate();
  const matching = useStartMatching();

  const ready = file !== null && category !== "" && manufacturers.length > 0;

  function reset() {
    setFile(null);
    setCategory("");
    setManufacturers([]);
    matching.reset();
  }

  function onSubmit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file === null || !ready) return;
    matching.mutate(
      { file, category, manufacturers },
      {
        onSuccess: () => {
          void navigate("/runs");
        },
      },
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6" aria-label="Upload tender">
      <div className="grid gap-4 md:grid-cols-3">
        <StepCard
          number={1}
          label="Tender"
          value={file?.name ?? "No file chosen"}
          done={file !== null}
          icon={<FileIcon />}
        />
        <StepCard
          number={2}
          label="Manufacturers"
          value={
            manufacturers.length > 0 ? `${String(manufacturers.length)} selected` : "None selected"
          }
          done={manufacturers.length > 0}
          icon={<FactoryIcon />}
        />
        <StepCard
          number={3}
          label="Matching"
          value={matching.isPending ? "Starting..." : ready ? "Ready to start" : "Waiting"}
          done={matching.isSuccess}
          icon={<PlayIcon />}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Tender document" icon={<FileIcon />}>
          <FileDropzone file={file} onChange={setFile} disabled={matching.isPending} />
        </Card>
        <Card title="Compare against" icon={<FactoryIcon />}>
          <ManufacturerPicker
            category={category}
            selected={manufacturers}
            onCategoryChange={setCategory}
            onSelectedChange={setManufacturers}
            disabled={matching.isPending}
          />
        </Card>
      </div>

      {matching.error !== null && (
        <Alert tone="error" title="Matching could not start">
          {matching.error.isUnavailable
            ? "Cannot reach the upload API. Your selection is kept."
            : matching.error.message}
        </Alert>
      )}

      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={reset} disabled={matching.isPending}>
          Clear
        </Button>
        <Button type="submit" icon={<PlayIcon />} disabled={!ready || matching.isPending}>
          {matching.isPending ? "Starting..." : "Start matching"}
        </Button>
      </div>
    </form>
  );
}
