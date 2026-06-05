import { useState } from "react";

/**
 * Avatar do Prism — sempre fundo branco (regra do design).
 * Carrega `/prism.png`; em fallback, mostra placeholder neutro com "P".
 */
export function PrismAvatar({
  size = 36,
  ring = false,
  className = "",
}: {
  size?: number;
  ring?: boolean;
  /** Tamanho ou ring */
  glow?: boolean; // mantém compat com chamadas antigas (ignorado)
  className?: string;
}) {
  const [imgErro, setImgErro] = useState(false);
  return (
    <span
      className={`prism-badge ${ring ? "ring" : ""} ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {!imgErro ? (
        <img
          src="/prism.png"
          alt="Prism"
          width={size}
          height={size}
          onError={() => setImgErro(true)}
        />
      ) : (
        <span
          style={{
            display: "grid",
            placeItems: "center",
            width: "100%",
            height: "100%",
            background: "linear-gradient(135deg,#5330C0,#44279C)",
            color: "#fff",
            fontSize: Math.max(10, Math.round(size * 0.42)),
            fontWeight: 700,
          }}
        >
          P
        </span>
      )}
    </span>
  );
}

export default PrismAvatar;
