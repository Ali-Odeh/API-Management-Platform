/** A label and value list, for the facts of one object. */
export function Details({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Shows a JSON value, pretty-printed. */
export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}
