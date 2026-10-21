export function show(value: string | number | null): string {
  if (value === null) {
    return "null";
  }
  return String(value);
}
