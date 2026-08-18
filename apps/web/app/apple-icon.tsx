import { ImageResponse } from "next/og";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0a0a",
          borderRadius: 40,
        }}
      >
        <svg
          fill="none"
          height="108"
          viewBox="0 0 32 32"
          width="108"
          xmlns="http://www.w3.org/2000/svg"
        >
          <path
            d="M7.5 12.25c0-.97.78-1.75 1.75-1.75h3.1c.4 0 .78.14 1.09.39l1.36 1.12c.31.25.7.39 1.09.39h5.86c.97 0 1.75.78 1.75 1.75v7.6c0 .97-.78 1.75-1.75 1.75H9.25c-.97 0-1.75-.78-1.75-1.75v-9.5Z"
            fill="#fafafa"
          />
        </svg>
      </div>
    ),
    size,
  );
}
