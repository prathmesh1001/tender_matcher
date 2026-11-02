import { Alert } from "../../components/ui/Alert";
import { Skeleton } from "../../components/ui/Skeleton";
import { useManufacturers } from "../../hooks/queries";
import type { Manufacturer } from "../../types/api";
import { groupVendors } from "../../lib/vendorGroups";
import { ManufacturerGroup } from "./ManufacturerGroup";

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

  const groups = groupVendors(data.filter((m) => m.category === category));

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
        <fieldset disabled={disabled} className="space-y-4">
          <legend className="mb-2 text-sm font-medium">Manufacturers to compare</legend>
          {groups.map((g) => (
            <ManufacturerGroup
              key={g.id}
              group={g}
              selected={selected}
              onSelectedChange={onSelectedChange}
            />
          ))}
        </fieldset>
      )}
    </div>
  );
}
