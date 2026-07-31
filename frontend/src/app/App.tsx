import { useEffect } from "react";
import { RouterProvider } from "react-router-dom";
import { bootstrapAuth } from "./bootstrap";
import { AppProviders } from "./providers";
import { router } from "./router";

export function App() {
  useEffect(() => {
    void bootstrapAuth();
  }, []);

  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
