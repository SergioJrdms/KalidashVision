import { useState } from "react";

/**
 * Avatar do Prism. Tenta carregar `/prism.png` (você dropa o arquivo em
 * frontend/public/prism.png e ele assume sozinho — sem mexer em código).
 * Enquanto a imagem não estiver lá, mostra um placeholder neutro com a
 * letra "P", para não travar a UI.
 */
export function PrismAvatar({
  size = 36,
  glow = true,
  className = "",
}: {
  size?: number;
  glow?: boolean;
  className?: string;
}) {
  const [imgErro, setImgErro] = useState(false);
  const dim = { width: size, height: size };

  return (
    <span
      className={`relative inline-flex items-center justify-center rounded-full overflow-visible flex-shrink-0 ${className}`}
      style={dim}
    >
      {glow && (
        <span
          aria-hidden
          className="absolute inset-0 rounded-full"
          style={{
            background:
              "radial-gradient(closest-side, rgba(167,139,250,0.55), rgba(124,58,237,0.18) 60%, transparent 75%)",
            filter: "blur(6px)",
            transform: "scale(1.4)",
            pointerEvents: "none",
          }}
        />
      )}
      <span
        className="relative inline-flex items-center justify-center rounded-full overflow-hidden bg-gradient-to-br from-kv-purple to-kv-purple-dark text-white font-semibold"
        style={dim}
      >
        {!imgErro ? (
          <img
            src="/prism.png"
            alt="Prism"
            width={size}
            height={size}
            className="w-full h-full object-cover"
            onError={() => setImgErro(true)}
          />
        ) : (
          <span style={{ fontSize: Math.round(size * 0.42) }}>P</span>
        )}
      </span>
    </span>
  );
}
