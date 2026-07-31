import { PageHeader } from "./PageHeader";

interface PlaceholderPageProps {
  title: string;
  description: string;
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <section className="rounded-lg border border-ink/10 bg-white p-5 shadow-sm">
      <PageHeader title={title} description={description} />
      <div className="rounded-md bg-mint/60 px-3 py-2 text-sm text-pine">
        Phase 1 只完成工程骨架，业务页面将在下一阶段接入真实 API。
      </div>
    </section>
  );
}
