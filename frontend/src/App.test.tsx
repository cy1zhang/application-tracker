import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import App from "./App";

test("identifies the product and current milestone", () => {
  render(<App />);

  expect(
    screen.getByRole("heading", { name: "Application Tracker" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Manual tracking is the first milestone."),
  ).toBeInTheDocument();
});
