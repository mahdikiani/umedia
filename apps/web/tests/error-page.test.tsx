// Owner's rule (2026-11-16): a user never sees React error text, a stack
// trace or a developer page. When rendering fails the app shows a plain
// page (one sentence, reload button, home link, support link) that needs no
// theme or provider, and the error still goes to error tracking.
import { render } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, test, vi } from "vitest";

const { captureException } = vi.hoisted(() => ({ captureException: vi.fn() }));
vi.mock("@sentry/nextjs", () => ({ captureException }));

import AppError from "@/app/error";
import GlobalError from "@/app/global-error";

function fakeError() {
  const error = new TypeError(
    "Cannot read properties of undefined (reading 'map')",
  ) as Error & { digest?: string };
  error.stack = `TypeError: ${error.message}\n    at FilesView (webpack-internal:///./components/files-view.js:12:7)`;
  error.digest = "1234567890";
  return error;
}

function assertPlain(html: string) {
  expect(html).toContain("Something went wrong.");
  expect(html).toContain("Reload");
  expect(html).toMatch(/href="\/"/);
  expect(html).toContain("mailto:");
  for (const leak of [
    "TypeError",
    "Error:",
    " at ",
    "Cannot read properties",
    "1234567890",
    "Application error",
  ]) {
    expect(html).not.toContain(leak);
  }
  expect(html).not.toMatch(/[\w-]+\.js\b/);
}

afterEach(() => {
  captureException.mockClear();
  window.localStorage.clear();
});

describe("plain error page", () => {
  test("global error page renders plainly with no provider", () => {
    const html = renderToStaticMarkup(
      <GlobalError error={fakeError()} reset={() => {}} />,
    );
    assertPlain(html);
    expect(html).toContain("<html");
  });

  test("route error page renders plainly with no provider", () => {
    const html = renderToStaticMarkup(
      <AppError error={fakeError()} reset={() => {}} />,
    );
    assertPlain(html);
  });

  test("the error still goes to error tracking", () => {
    const error = fakeError();
    render(<AppError error={error} reset={() => {}} />);
    expect(captureException).toHaveBeenCalledWith(error);
  });

  test("Persian interface gets the Persian sentence", () => {
    window.localStorage.setItem("umedia_locale", "fa");
    const { container } = render(
      <AppError error={fakeError()} reset={() => {}} />,
    );
    expect(container.textContent).toContain("مشکلی پیش آمد.");
    expect(container.textContent).toContain("بارگیری دوباره");
    expect(container.textContent).not.toContain("TypeError");
  });
});
