import logoUrl from "../../assets/langalpha-logo.png";

interface BrandMarkProps {
  className?: string;
}

export function BrandMark({ className }: BrandMarkProps) {
  return (
    <img
      alt=""
      aria-hidden="true"
      className={className}
      src={logoUrl}
    />
  );
}
