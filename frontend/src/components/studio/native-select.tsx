export function NativeSelect({ value, onChange, children, id }: {
  value: string; onChange: (value: string) => void; children: React.ReactNode; id?: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-9 w-full rounded-md border bg-background px-2 text-sm"
    >
      {children}
    </select>
  );
}
