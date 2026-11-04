import { describe, expect, it } from "vitest";

import { formatBytes, validateTenderFile } from "./files";

const file = (name: string, type: string, size = 10) =>
  new File([new Uint8Array(size)], name, { type });

describe("validateTenderFile", () => {
  it("accepts a PDF", () => {
    expect(validateTenderFile(file("bid.pdf", "application/pdf"))).toBeNull();
  });

  it("accepts a PDF with no MIME type (Windows) and an upper-case extension", () => {
    expect(validateTenderFile(file("BID.PDF", ""))).toBeNull();
  });

  it.each([
    ["tender.zip", "application/zip"],
    ["tender.zip", "application/x-zip-compressed"],
    ["TENDER.ZIP", ""],
  ])("accepts the tender ZIP %s (%s)", (name, type) => {
    expect(validateTenderFile(file(name, type))).toBeNull();
  });

  it.each([
    ["spec.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    ["boq.xlsx", ""],
    ["fake.pdf", "image/png"],
  ])("rejects %s", (name, type) => {
    expect(validateTenderFile(file(name, type))).toMatch(/Only \.pdf/);
  });

  it("rejects an empty file", () => {
    expect(validateTenderFile(file("bid.pdf", "application/pdf", 0))).toBe("The file is empty.");
  });
});

describe("formatBytes", () => {
  it("formats KB and MB", () => {
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
