import type { Manufacturer } from "../types/api";

/** Vendors of one product family ("Valves", "Pumps"), with the types they make in it. */
export interface VendorGroup {
  id: string;
  title: string;
  vendors: { manufacturer: Manufacturer; types: string[] }[];
}

const FAMILIES: { id: string; title: string; suffix: string }[] = [
  { id: "valves", title: "Valves", suffix: "_valve" },
  { id: "pumps", title: "Pumps", suffix: "_pump" },
];

/**
 * Split manufacturers into Valves, Pumps and Other by the product types they declare.
 * A vendor that makes both (KSB, Kirloskar) is listed in both, with only that family's types.
 */
export function groupVendors(manufacturers: Manufacturer[]): VendorGroup[] {
  const groups: VendorGroup[] = FAMILIES.map(({ id, title, suffix }) => ({
    id,
    title,
    vendors: manufacturers
      .map((m) => ({ manufacturer: m, types: m.product_types.filter((t) => t.endsWith(suffix)) }))
      .filter((v) => v.types.length > 0),
  }));
  const other = manufacturers
    .map((m) => ({
      manufacturer: m,
      types: m.product_types.filter((t) => !FAMILIES.some((f) => t.endsWith(f.suffix))),
    }))
    .filter((v) => v.types.length > 0 || v.manufacturer.product_types.length === 0);
  if (other.length > 0) groups.push({ id: "other", title: "Other", vendors: other });
  return groups.filter((g) => g.vendors.length > 0);
}
