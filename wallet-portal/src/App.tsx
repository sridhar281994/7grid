import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from "react-router-dom";
import LinkPage from "./pages/LinkPage";
import Dashboard from "./pages/Dashboard";
import Recharge from "./pages/Recharge";
import Withdraw from "./pages/Withdraw";
import History from "./pages/History";
import Admin from "./pages/Admin";
import WalletSessionGate from "./components/WalletSessionGate";

function RedirectToDashboard() {
  const location = useLocation();

  return (
    <Navigate
      to={{
        pathname: "/wallet/dashboard",
        search: location.search,
        hash: location.hash,
      }}
      replace
    />
  );
}

function App() {
  return (
    <BrowserRouter>
      <WalletSessionGate>
        <Routes>
          <Route path="/" element={<RedirectToDashboard />} />
          <Route path="/link" element={<LinkPage />} />
          <Route path="/wallet/dashboard" element={<Dashboard />} />
          <Route path="/wallet/recharge" element={<Recharge />} />
          <Route path="/wallet/withdraw" element={<Withdraw />} />
          <Route path="/wallet/history" element={<History />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<RedirectToDashboard />} />
        </Routes>
      </WalletSessionGate>
    </BrowserRouter>
  );
}

export default App;
