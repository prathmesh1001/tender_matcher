import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "./components/layout/AppLayout";
import { ReportPage } from "./pages/ReportPage";
import { ReportsPage } from "./pages/ReportsPage";
import { RunsPage } from "./pages/RunsPage";
import { UploadPage } from "./pages/UploadPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/upload" replace />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/:runId" element={<ReportPage />} />
        <Route path="*" element={<Navigate to="/upload" replace />} />
      </Route>
    </Routes>
  );
}

export function App() {
  const [client] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
