import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import Dashboard from "./pages/Dashboard";
import { IngredientDetail, IngredientForm, IngredientsPage } from "./pages/Ingredients";
import Login from "./pages/Login";
import { NewPurchase, PurchaseDetail, PurchasesPage } from "./pages/Purchases";
import { RecipeDetail, RecipeEditor } from "./pages/Recipes";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import { CountSheet, StockPage } from "./pages/Stock";
import { SupplierDetail, SuppliersPage } from "./pages/Suppliers";

export default function App() {
  const { me, loading } = useAuth();
  if (loading) return <Loading label="Starting…" />;
  if (!me) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="ingredients" element={<IngredientsPage />} />
        <Route path="ingredients/new" element={<IngredientForm />} />
        <Route path="ingredients/:id" element={<IngredientDetail />} />
        <Route path="ingredients/:id/edit" element={<IngredientForm />} />
        <Route path="recipes" element={<Navigate to="/ingredients?tab=recipes" replace />} />
        <Route path="recipes/new" element={<RecipeEditor />} />
        <Route path="recipes/:id" element={<RecipeDetail />} />
        <Route path="recipes/:id/edit" element={<RecipeEditor />} />
        <Route path="purchases" element={<PurchasesPage />} />
        <Route path="purchases/new" element={<NewPurchase />} />
        <Route path="purchases/:id" element={<PurchaseDetail />} />
        <Route path="suppliers" element={<SuppliersPage />} />
        <Route path="suppliers/:id" element={<SupplierDetail />} />
        <Route path="stock" element={<StockPage />} />
        <Route path="stock/counts/:id" element={<CountSheet />} />
        <Route path="reports" element={<Reports />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
