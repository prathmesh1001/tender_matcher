import { PageBanner } from "../components/layout/PageBanner";
import { UploadForm } from "../features/upload/UploadForm";

export function UploadPage() {
  return (
    <>
      <PageBanner
        title="Match a tender"
        subtitle="Upload a GeM tender, choose manufacturers, and get every requirement checked against their catalogues with page-level evidence."
      />
      <UploadForm />
    </>
  );
}
