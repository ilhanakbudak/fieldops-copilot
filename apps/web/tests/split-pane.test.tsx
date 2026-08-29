/**
 * The resizable split, and the labelled rows a tool result renders as.
 *
 * Both are small components and neither is tested for looking right. What is
 * worth asserting is the part that is easy to get wrong and invisible when it
 * is: a divider that only a mouse can move, and a renderer that drops a value
 * it did not expect.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { ResultRow } from "@fieldops/shared";

import { ResultRows } from "@/components/ResultRows";
import { SplitPane } from "@/components/SplitPane";

function renderSplit(props: Partial<Parameters<typeof SplitPane>[0]> = {}) {
  return render(
    <SplitPane
      storageKey="test.split"
      start={<p>Transcript</p>}
      end={<p>Suggestions</p>}
      initial={50}
      min={20}
      max={80}
      {...props}
    />,
  );
}

describe("SplitPane", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("is a separator a keyboard can reach", () => {
    renderSplit();
    const divider = screen.getByRole("separator");

    expect(divider).toHaveAttribute("aria-valuenow", "50");
    expect(divider).toHaveAttribute("aria-valuemin", "20");
    expect(divider).toHaveAttribute("aria-valuemax", "80");
    // A "resizable layout" nobody can resize without a mouse is not one.
    expect(divider).toHaveAttribute("tabindex", "0");
  });

  it("moves with the arrow keys, and further with shift", async () => {
    const user = userEvent.setup();
    renderSplit();
    const divider = screen.getByRole("separator");

    divider.focus();
    await user.keyboard("{ArrowRight}");
    expect(divider).toHaveAttribute("aria-valuenow", "52");

    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(divider).toHaveAttribute("aria-valuenow", "42");
  });

  it("will not be dragged past its bounds", async () => {
    const user = userEvent.setup();
    renderSplit();
    const divider = screen.getByRole("separator");

    divider.focus();
    await user.keyboard("{End}");
    expect(divider).toHaveAttribute("aria-valuenow", "80");

    // Already at the maximum; another step must not take it further.
    await user.keyboard("{ArrowRight}");
    expect(divider).toHaveAttribute("aria-valuenow", "80");

    await user.keyboard("{Home}");
    expect(divider).toHaveAttribute("aria-valuenow", "20");
  });

  it("remembers where it was put", async () => {
    const user = userEvent.setup();
    const { unmount } = renderSplit();

    screen.getByRole("separator").focus();
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(window.localStorage.getItem("test.split")).toBe("60");

    unmount();
    renderSplit();
    expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "60");
  });

  it("ignores a stored position outside the bounds", () => {
    // A `min` that changed between releases, or somebody editing devtools.
    window.localStorage.setItem("test.split", "95");
    renderSplit();

    expect(screen.getByRole("separator")).toHaveAttribute("aria-valuenow", "50");
  });

  it("labels both panels, so the separator is not the only landmark", () => {
    renderSplit({ startLabel: "Transcript", endLabel: "Suggestions" });

    expect(screen.getByRole("region", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Suggestions" })).toBeInTheDocument();
  });
});

describe("ResultRows", () => {
  it("renders a label with its value", () => {
    const rows: ResultRow[] = [
      { label: "Timezone", value: "America/New_York" },
      { label: "DST", value: "yes" },
    ];
    render(<ResultRows rows={rows} />);

    expect(screen.getByText("Timezone")).toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
  });

  it("keeps a group together under its own heading", () => {
    // A "source" and a "target" are meant to be compared. Flattening them into
    // six sibling rows loses which timezone each value belongs to.
    const rows: ResultRow[] = [
      { label: "Source", group: [{ label: "Timezone", value: "UTC" }] },
      { label: "Target", group: [{ label: "Timezone", value: "Europe/Istanbul" }] },
    ];
    render(<ResultRows rows={rows} />);

    const source = screen.getByText("Source").closest("div");
    expect(source).not.toBeNull();
    expect(within(source as HTMLElement).getByText("UTC")).toBeInTheDocument();
    expect(within(source as HTMLElement).queryByText("Europe/Istanbul")).toBeNull();
  });

  it("renders nothing at all when there is nothing to render", () => {
    // Rather than an empty bordered box in the middle of a transcript.
    const { container } = render(<ResultRows rows={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
