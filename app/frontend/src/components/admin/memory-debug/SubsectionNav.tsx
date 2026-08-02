import { Button } from "@/components/ui/button";

export function SubsectionNav<T extends string>({
  items,
  activeItem,
  onChange,
}: {
  items: Array<{ id: T; label: string; description: string }>;
  activeItem: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <Button
          key={item.id}
          size="sm"
          variant={activeItem === item.id ? "default" : "outline"}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </Button>
      ))}
    </div>
  );
}
