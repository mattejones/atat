import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ATAT",
    short_name: "ATAT",
    description: "Job applications, CVs and cover letters in one place.",
    start_url: "/",
    display: "standalone",
    background_color: "#F0EDE8",
    theme_color: "#F0EDE8",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
    // Lets the phone's Share sheet send a job link straight to the New Application page.
    // Next 14's Manifest type models `params` as an array, which isn't what the Web Share
    // Target spec says (an object of param names), so the cast is deliberate.
    share_target: {
      action: "/generate",
      method: "get",
      params: { title: "title", text: "text", url: "url" },
    },
  } as unknown as MetadataRoute.Manifest;
}
