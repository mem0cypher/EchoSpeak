import React from 'react';
import { BrowserRouter as Router, MemoryRouter, Routes, Route } from 'react-router-dom';
import { Marketing } from './marketing.tsx';
import { Dashboard } from './index.tsx';
import { DesktopApp } from './desktop/DesktopApp.tsx';
import { isDesktopRuntime } from './desktop/bridge.ts';

const App: React.FC = () => {
    if (isDesktopRuntime()) {
        return (
            <MemoryRouter initialEntries={['/app']}>
                <DesktopApp />
            </MemoryRouter>
        );
    }
    return (
        <Router>
            <Routes>
                <Route path="/" element={<Marketing />} />
                <Route path="/app/*" element={<Dashboard />} />
            </Routes>
        </Router>
    );
};

export default App;
