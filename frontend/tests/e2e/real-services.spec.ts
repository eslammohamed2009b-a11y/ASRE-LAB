import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const ready = Boolean(process.env.NEXT_PUBLIC_FASTAPI_API_URL && process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

test("public navigation and accessibility", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /concept to evidence-backed decision/i })).toBeVisible();
  await page.getByRole("link", { name: "Scientific Scope" }).first().click();
  await expect(page.getByRole("heading", { name: "Boundaries before buttons." })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("anonymous Supabase session reaches the protected backend", async ({ page }) => {
  test.skip(!ready, "Real-service E2E configuration is not available.");
  await page.goto("/auth/log-in");
  await page.getByRole("button", { name: "Start ASRE-Lab" }).click();
  await expect(page).toHaveURL(/\/app\/dashboard/);
  await expect(page.getByRole("heading", { name: "Persisted studies" })).toBeVisible();
  await expect(page.getByText("Guest researcher")).toBeVisible();
  await expect(page.getByText("Unable to load your studies.")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL("/");
});
