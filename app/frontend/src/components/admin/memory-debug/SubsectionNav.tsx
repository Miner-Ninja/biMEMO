import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { TranslationKey } from "@/i18n";

export function SubsectionNav<T extends string>({
  items,
  activeItem,
  onChange,
}: {
  items: Array<{ id: T; labelKey: TranslationKey; descriptionKey: TranslationKey }>;
  activeItem: T;
  onChange: (id: T) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <Button
          key={item.id}
          size="sm"
          variant={activeItem === item.id ? "default" : "outline"}
          title={t(item.descriptionKey)}
          onClick={() => onChange(item.id)}
        >
          {t(item.labelKey)}
        </Button>
      ))}
    </div>
  );
}
