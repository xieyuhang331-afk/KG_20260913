interface PageHeaderProps {
  title: string;
  description?: string;
}

export function PageHeader({ title, description }: PageHeaderProps) {
  return (
    <header className="mb-6">
      <p className="text-xs font-semibold uppercase text-pine/70">康邻 P1</p>
      <h1 className="mt-2 text-2xl font-semibold text-ink">{title}</h1>
      {description ? <p className="mt-2 max-w-2xl text-sm text-ink/65">{description}</p> : null}
    </header>
  );
}
