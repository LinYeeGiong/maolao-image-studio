import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import ImageGenerator from "./ImageGenerator"
import "./index.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ImageGenerator />
  </StrictMode>,
)
