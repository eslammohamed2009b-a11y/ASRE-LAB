import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const ready = Boolean(process.env.E2E_USER_EMAIL && process.env.E2E_USER_PASSWORD && process.env.NEXT_PUBLIC_FASTAPI_API_URL && process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

test("public navigation and accessibility", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /concept to evidence-backed decision/i })).toBeVisible();
  await page.getByRole("link", { name: "Scientific Scope" }).first().click();
  await expect(page.getByRole("heading", { name: "Boundaries before buttons." })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("real Supabase session, protected dashboard, and logout", async ({ page }) => {
  test.skip(!ready, "Real-service E2E credentials are not configured.");
  await page.goto("/auth/log-in");
  await page.getByLabel("Email address").fill(process.env.E2E_USER_EMAIL!);
  await page.getByLabel("Password").fill(process.env.E2E_USER_PASSWORD!);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/app\/dashboard/);
  await expect(page.getByText("SESSION ACTIVE")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Log Out" }).click();
  await expect(page).toHaveURL("/");
});
