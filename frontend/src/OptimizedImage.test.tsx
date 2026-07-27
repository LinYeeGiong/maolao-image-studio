import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import OptimizedImage from "./OptimizedImage"

describe("OptimizedImage", () => {
  it("keeps rendering while an uploaded file preview URL is not ready", () => {
    expect(() =>
      renderToStaticMarkup(
        <OptimizedImage
          src={undefined}
          alt="上传的参考图"
          loading="eager"
        />,
      ),
    ).not.toThrow()
  })
})
