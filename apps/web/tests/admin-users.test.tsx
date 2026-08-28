/**
 * The employee administration screen.
 *
 * What is worth testing here is the *refusals*. Rendering a table of names is
 * not where this screen goes wrong; it goes wrong when an administrator demotes
 * themselves, or disables the last remaining administrator, and the interface
 * quietly reports success because it never looked at the response.
 *
 * The API is mocked at the module boundary rather than over the network: this
 * asserts what the screen does with an answer, and `tests/test_admin.py`
 * already asserts that the API gives the right one.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MeResponse, Permission, UserSummary } from "@fieldops/shared";

import { ApiError } from "@/lib/api";
import UsersPage from "@/app/(app)/admin/users/page";

const { apiMock, sessionMock } = vi.hoisted(() => ({
  apiMock: {
    users: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
  },
  sessionMock: { current: null as MeResponse | null },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  // `ApiError` is a real class the component checks with `instanceof`, so the
  // module is spread rather than replaced — only `api` is swapped.
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, api: apiMock };
});

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    session: sessionMock.current,
    status: "signed-in" as const,
    can: (permission: Permission) =>
      sessionMock.current?.permissions.includes(permission) ?? false,
    signIn: vi.fn(),
    signOut: vi.fn(),
  }),
}));

vi.mock("@/components/AppShell", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

function user(overrides: Partial<UserSummary> = {}): UserSummary {
  return {
    id: "u-1",
    email: "admin@example.com",
    fullName: "Dana Okafor",
    role: "admin",
    isActive: true,
    lastLoginAt: "2026-08-28T09:00:00Z",
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function signedInAs(role: UserSummary["role"], permissions: Permission[], id = "u-1"): void {
  sessionMock.current = { user: user({ id, role }), permissions, documentRoles: [] };
}

const OTHER = user({
  id: "u-2",
  email: "tech@example.com",
  fullName: "Sam Whitfield",
  role: "technician",
});

beforeEach(() => {
  signedInAs("admin", ["users:manage"]);
  apiMock.users.mockResolvedValue([user(), OTHER]);
  apiMock.updateUser.mockResolvedValue(OTHER);
  apiMock.createUser.mockResolvedValue(OTHER);
});

describe("access", () => {
  it("tells a role without the permission that the screen is not for them", async () => {
    signedInAs("technician", ["documents:read"]);

    render(<UsersPage />);

    expect(await screen.findByText(/not available for your role/i)).toBeInTheDocument();
    // And does not even ask: the screen is a courtesy, the API is the control.
    expect(apiMock.users).not.toHaveBeenCalled();
  });

  it("lists every account for an administrator", async () => {
    render(<UsersPage />);

    expect(await screen.findByText("Sam Whitfield")).toBeInTheDocument();
    expect(screen.getByText("tech@example.com")).toBeInTheDocument();
    expect(screen.getByText("2 accounts")).toBeInTheDocument();
  });

  it("surfaces a failure to load rather than showing an empty table", async () => {
    apiMock.users.mockRejectedValue(new ApiError("forbidden", "Nope.", 403));

    render(<UsersPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Nope.");
  });
});

describe("changing an account", () => {
  it("disables an employee", async () => {
    render(<UsersPage />);
    const row = (await screen.findByText("Sam Whitfield")).closest("tr")!;

    await userEvent.click(within(row).getByRole("button", { name: /disable/i }));

    await waitFor(() =>
      expect(apiMock.updateUser).toHaveBeenCalledWith("u-2", { isActive: false }),
    );
    // Reloaded, so the table shows what the server now holds rather than what
    // the click assumed.
    expect(apiMock.users).toHaveBeenCalledTimes(2);
  });

  it("changes a role", async () => {
    render(<UsersPage />);
    await screen.findByText("Sam Whitfield");

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /role for sam whitfield/i }),
      "office",
    );

    await waitFor(() => expect(apiMock.updateUser).toHaveBeenCalledWith("u-2", { role: "office" }));
  });

  it("will not let an administrator change their own account", async () => {
    /**
     * The first of two guards against locking everyone out. Enforced by the API
     * as well; disabling the controls is what stops somebody discovering the
     * rule by hitting it.
     */
    render(<UsersPage />);
    const row = (await screen.findByText(/Dana Okafor/)).closest("tr")!;

    expect(within(row).getByRole("button", { name: /disable/i })).toBeDisabled();
    expect(within(row).getByRole("combobox")).toBeDisabled();
    expect(within(row).getByText(/\(you\)/)).toBeInTheDocument();
  });

  it("shows the refusal when the server rejects a change", async () => {
    /**
     * The second guard lives only on the server — whether an account is the
     * last active administrator is not something the browser can know. So the
     * screen has to read the response and say what came back.
     */
    apiMock.updateUser.mockRejectedValue(
      new ApiError("conflict", "The last active administrator cannot be disabled.", 409),
    );

    render(<UsersPage />);
    const row = (await screen.findByText("Sam Whitfield")).closest("tr")!;

    await userEvent.click(within(row).getByRole("button", { name: /disable/i }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      /last active administrator/i,
    );
  });
});

describe("adding an employee", () => {
  it("creates an account and clears the form", async () => {
    render(<UsersPage />);
    await screen.findByText("Sam Whitfield");

    await userEvent.type(screen.getByLabelText(/full name/i), "Priya Raman");
    await userEvent.type(screen.getByLabelText(/^email$/i), "p.raman@example.com");
    await userEvent.type(screen.getByLabelText(/temporary password/i), "a-long-enough-password");
    await userEvent.selectOptions(screen.getByLabelText(/^role$/i), "sales");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() =>
      expect(apiMock.createUser).toHaveBeenCalledWith({
        fullName: "Priya Raman",
        email: "p.raman@example.com",
        password: "a-long-enough-password",
        role: "sales",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(/can now sign in/i);
    expect(screen.getByLabelText(/full name/i)).toHaveValue("");
  });

  it("requires a password long enough to survive a wordlist", async () => {
    render(<UsersPage />);
    await screen.findByText("Sam Whitfield");

    const password = screen.getByLabelText(/temporary password/i);

    // The floor is enforced by the API too; this is the browser refusing to
    // send a request it already knows will fail.
    expect(password).toHaveAttribute("minLength", "12");
  });

  it("reports a duplicate email instead of appearing to succeed", async () => {
    apiMock.createUser.mockRejectedValue(
      new ApiError("conflict", "An account with that email already exists.", 409),
    );

    render(<UsersPage />);
    await screen.findByText("Sam Whitfield");

    await userEvent.type(screen.getByLabelText(/full name/i), "Someone Else");
    await userEvent.type(screen.getByLabelText(/^email$/i), "tech@example.com");
    await userEvent.type(screen.getByLabelText(/temporary password/i), "a-long-enough-password");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
  });
});
