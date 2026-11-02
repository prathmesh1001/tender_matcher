import { Alert } from "../../components/ui/Alert";
import { Skeleton } from "../../components/ui/Skeleton";
import { useManufacturers } from "../../hooks/queries";
import type { Manufacturer } from "../../types/api";

interface ManufacturerPickerProps {
  category: string;
  selected: string[];
  onCategoryChange: (category: string) => void;
  onSelectedChange: (names: string[]) => void;
  disabled?: boolean;
}

function categoriesOf(manufacturers: Manufacturer[]): string[] {
  return [...new Set(manufacturers.map((m) => m.category))].sort();
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

/** Category select + manufacturer checkboxes, filled from GET /manufacturers. */
export function ManufacturerPicker({
  category,
  selected,
  onCategoryChange,
  onSelectedChange,
  disabled = false,
}: ManufacturerPickerProps) {
  const { data, isPending, error } = useManufacturers();

  if (isPending) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading manufacturers">
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-5 w-72" />
        <Skeleton className="h-5 w-64" />
      </div>
    );
  }
  if (error !== null) {
    return (
      <Alert tone="error" title="Manufacturer list is not available">
        {error.isUnavailable
          ? "Cannot reach the backend, so manufacturers cannot be listed."
          : error.message}
      </Alert>
    );
  }
  if (data.length === 0) {
    return (
      <Alert title="No manufacturers yet">
        Ingest catalogues first: <code>python -m app.cli ingest-manufacturers</code>
      </Alert>
    );
  }

  const shown = data.filter((m) => m.category === category);
  const toggle = (name: string) => {
    onSelectedChange(
      selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name],
    );
  };

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="category" className="mb-1 block text-sm font-medium">
          Product category
        </label>
        <select
          id="category"
          value={category}
          disabled={disabled}
          onChange={(e) => {
            onCategoryChange(e.target.value);
            onSelectedChange([]);
          }}
          className="w-64 rounded-lg border border-line bg-white px-3 py-2 text-sm capitalize focus-visible:outline-2 focus-visible:outline-brand-600"
        >
          <option value="">Choose a category</option>
          {categoriesOf(data).map((c) => (
            <option key={c} value={c}>
              {label(c)}
            </option>
          ))}
        </select>
      </div>
      {category !== "" && (
        <fieldset disabled={disabled}>
          <legend className="mb-2 text-sm font-medium">Manufacturers to compare</legend>
          <ul className="divide-y divide-line rounded-lg border border-line">
            {shown.map((m) => (
              <li key={m.name}>
                <label className="flex cursor-pointer items-center gap-3 px-4 py-3 hover:bg-canvas">
                  <input
                    type="checkbox"
                    checked={selected.includes(m.name)}
                    onChange={() => {
                      toggle(m.name);
                    }}
                    className="size-4 accent-brand-600"
                  />
                  <span className="flex-1 font-medium">{label(m.name)}</span>
                  <span className="text-xs text-ink-600">
                    {m.documents} catalogue{m.documents === 1 ? "" : "s"} ·{" "}
                    {m.product_types.map(label).join(", ")}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
    </div>
  );
}
