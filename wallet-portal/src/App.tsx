import { BrowserRouter, Routes, Route } from "react-router-dom";
import LinkPage from "./pages/LinkPage";
import Dashboard from "./pages/Dashboard";
import Recharge from "./pages/Recharge";
import Withdraw from "./pages/Withdraw";
import History from "./pages/History";
import Admin from "./pages/Admin";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/link" element={<LinkPage />} />
        <Route path="/wallet/dashboard" element={<Dashboard />} />
        <Route path="/wallet/recharge" element={<Recharge />} />
        <Route path="/wallet/withdraw" element={<Withdraw />} />
        <Route path="/wallet/history" element={<History />} />
        <Route path="/admin" element={<Admin />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
