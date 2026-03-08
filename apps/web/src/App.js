import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Marketing } from './marketing';
import { Dashboard } from './index';
const App = () => {
    return (_jsx(Router, { children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Marketing, {}) }), _jsx(Route, { path: "/app", element: _jsx(Dashboard, {}) })] }) }));
};
export default App;
