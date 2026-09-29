import type { ReactElement } from "react";
import { render, type RenderOptions } from "@testing-library/react";

import { LocaleProvider } from "@/components/locale-provider";

export function renderWithLocale(ui: ReactElement, options?: RenderOptions) {
  return render(<LocaleProvider>{ui}</LocaleProvider>, options);
}
