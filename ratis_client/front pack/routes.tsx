import { createBrowserRouter, Navigate } from "react-router";
import { RootLayout } from "./components/RootLayout";
import { LoginScreen } from "./components/LoginScreen";
import { ListeScreen } from "./components/ListeScreen";
import { ScanScreen } from "./components/ScanScreen";
import { ProduitsScreen } from "./components/ProduitsScreen";
import { ProfilScreen } from "./components/ProfilScreen";
import { Dashboard } from "./components/Dashboard";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <LoginScreen />,
  },
  {
    path: "/RatisApp",
    element: <RootLayout />,
    children: [
      {
        index: true,
        element: <Navigate to="/RatisApp/dashboard" replace />,
      },
      {
        path: "dashboard",
        element: <Dashboard />,
      },
      {
        path: "liste",
        element: <ListeScreen />,
      },
      {
        path: "scan",
        element: <ScanScreen />,
      },
      {
        path: "produits",
        element: <ProduitsScreen />,
      },
      {
        path: "profil",
        element: <ProfilScreen />,
      },
    ],
  },
  {
    path: "*",
    element: <Navigate to="/" replace />,
  },
]);