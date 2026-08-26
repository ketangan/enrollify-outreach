// Serves files from the pontora-generated-sites R2 bucket, and resolves
// short links (/p/<code>) via a KV namespace before falling through to R2.
//
// Deployed once (see docs/setup_site_generator.md). After that, the Python
// side (src/r2_storage.py, src/shortlinks.py) writes new sites/short links
// directly — no redeploy needed to add or update either.
//
// Resolves directory-style URLs to index.html, e.g.
// /sites/some-business-abc123/preschool-warm/  ->  key "sites/.../index.html"

const CONTENT_TYPE_BY_EXT = {
  html: "text/html; charset=utf-8",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  woff2: "font/woff2",
  css: "text/css; charset=utf-8",
  js: "text/javascript; charset=utf-8",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Short links: /p/<code> -> 302 to whatever long URL is stored in KV.
    // Checked first since it's a distinct namespace from R2 object keys.
    const shortMatch = url.pathname.match(/^\/p\/([a-z0-9]+)$/);
    if (shortMatch) {
      const target = await env.SHORTLINKS.get(shortMatch[1]);
      if (target === null) {
        return new Response("Short link not found", { status: 404 });
      }
      return Response.redirect(target, 302);
    }

    let key = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    if (key === "" || key.endsWith("/")) {
      key = key + "index.html";
    }

    const object = await env.BUCKET.get(key);
    if (object === null) {
      return new Response("Not found", { status: 404 });
    }

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("etag", object.httpEtag);
    if (!headers.get("content-type")) {
      const ext = key.split(".").pop().toLowerCase();
      headers.set("content-type", CONTENT_TYPE_BY_EXT[ext] || "application/octet-stream");
    }
    headers.set("cache-control", "public, max-age=300");

    return new Response(object.body, { headers });
  },
};
