import { useEffect, useRef } from "react";

import type { VendorGroup } from "../../lib/vendorGroups";

function label(value: string): string {
  return value.replaceAll("_", " ");
}

interface ManufacturerGroupProps {
  group: VendorGroup;
  selected: string[];
  onSelectedChange: (names: string[]) => void;
}

/** One section of vendor checkboxes with a "Select all" box that can be half-checked. */
export function ManufacturerGroup({ group, selected, onSelectedChange }: ManufacturerGroupProps) {
  const names = group.vendors.map((v) => v.manufacturer.name);
  const chosen = names.filter((n) => selected.includes(n)).length;
  const all = chosen === names.length;
  const allRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (allRef.current !== null) allRef.current.indeterminate = chosen > 0 && !all;
  }, [chosen, all]);

  const toggleAll = () => {
    onSelectedChange(
      all ? selected.filter((n) => !names.includes(n)) : [...new Set([...selected, ...names])],
    );
  };
  const toggle = (name: string) => {
    onSelectedChange(
      selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name],
    );
  };
  const headingId = `vendors-${group.id}`;

  return (
    <section aria-labelledby={headingId}>
      <div className="mb-2 flex items-center justify-between">
        <h3 id={headingId} className="text-sm font-semibold">
          {group.title}{" "}
          <span className="font-normal text-ink-600">
            ({String(chosen)} of {String(names.length)})
          </span>
        </h3>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            ref={allRef}
            type="checkbox"
            checked={all}
            onChange={toggleAll}
            aria-label={`Select all ${group.title.toLowerCase()}`}
            className="size-4 accent-brand-600"
          />
          Select all
        </label>
      </div>
      <ul className="divide-y divide-line rounded-lg border border-line">
        {group.vendors.map(({ manufacturer: m, types }) => (
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
                {types.map(label).join(", ")}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}
