/**
 * Tests for FITSBrowser — the FITS file manager component.
 *
 * Tests cover rendering, empty state, and file list display.
 * API calls are mocked to avoid network requests.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { FITSFileInfo } from "../api/client";

// ── Mock i18n — pass keys through as-is ──
vi.mock("../i18n", () => ({
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

// ── Mock FITSPreview to avoid heavy rendering ──
vi.mock("../components/fits/FITSPreview", () => ({
  default: () => <div data-testid="fits-preview">Preview</div>,
}));

// ── Mock API client ──
const mockBrowseFITS = vi.fn<() => Promise<FITSFileInfo[]>>(() => Promise.resolve([]));
const mockUploadFITS = vi.fn(() => Promise.resolve({
  id: "test",
  filename: "test.fits",
  fits_path: "uploads/test.fits",
  size_bytes: 1024,
  source: "upload",
  object_id: "test",
  created_at: null,
  metadata: {},
}));
const mockDeleteFITS = vi.fn(() => Promise.resolve());

vi.mock("../api/client", () => ({
  browseFITS: (...args: unknown[]) => mockBrowseFITS(...args as []),
  uploadFITS: (...args: unknown[]) => mockUploadFITS(...args as []),
  deleteFITS: (...args: unknown[]) => mockDeleteFITS(...args as []),
  downloadFITSUrl: (path: string) => `/api/data/fits/download?fits_path=${path}`,
}));

// ── Import component under test (after mocks) ──
import FITSBrowser from "../components/fits/FITSBrowser";

/* ── Test data ── */

function makeFileInfo(overrides: Partial<FITSFileInfo> = {}): FITSFileInfo {
  return {
    id: "file-1",
    filename: "galaxy.fits",
    fits_path: "uploads/galaxy.fits",
    size_bytes: 2048000,
    source: "upload",
    object_id: "GAL-001",
    created_at: "2025-01-15T10:30:00Z",
    metadata: {},
    ...overrides,
  };
}

describe("FITSBrowser", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockBrowseFITS.mockResolvedValue([]);
  });

  // ── Title ──

  it("renders the FITS file manager title", async () => {
    render(<FITSBrowser />);
    await waitFor(() => {
      expect(screen.getByText("fits.manager_title")).toBeInTheDocument();
    });
  });

  // ── Empty state ──

  it("shows empty state when no files", async () => {
    mockBrowseFITS.mockResolvedValue([]);
    render(<FITSBrowser />);
    await waitFor(() => {
      expect(screen.getByText("fits.no_files")).toBeInTheDocument();
    });
  });

  // ── File list display ──

  it("handles file list display", async () => {
    const files: FITSFileInfo[] = [
      makeFileInfo({ id: "f1", filename: "star-field.fits", source: "sdss" }),
      makeFileInfo({ id: "f2", filename: "nebula.fits", source: "mast" }),
    ];
    mockBrowseFITS.mockResolvedValue(files);

    render(<FITSBrowser />);

    await waitFor(() => {
      expect(screen.getByText("star-field.fits")).toBeInTheDocument();
      expect(screen.getByText("nebula.fits")).toBeInTheDocument();
    });
  });
});
