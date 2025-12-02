import { BrowserRouter, Routes, Route } from "react-router-dom";
import LinkPage from "./pages/LinkPage";
import Dashboard from "./pages/Dashboard";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/link" element={<LinkPage />} />
        <Route path="/wallet/dashboard" element={<Dashboard />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
