import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Marketing } from './marketing';
import { Dashboard } from './index';

const App: React.FC = () => {
    return (
        <Router>
            <Routes>
                <Route path="/" element={<Marketing />} />
                <Route path="/app" element={<Dashboard />} />
            </Routes>
        </Router>
    );
};

export default App;
