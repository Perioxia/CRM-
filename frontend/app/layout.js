import './globals.css';
import { AuthProvider } from '../contexts/AuthContext';
import { ThemeProvider } from '../contexts/ThemeContext';
import { LocaleProvider } from '../contexts/LocaleContext';
import { NotificationProvider } from '../contexts/NotificationContext';
import { ChatProvider } from '../contexts/ChatContext';
import { CallProvider } from '../contexts/CallContext';
import { SoftphoneProvider } from '../contexts/SoftphoneContext';
import CallOverlay from '../components/chat/CallOverlay';
import SoftphoneBar from '../components/telephony/SoftphoneBar';
import Layout from '../components/Layout';
import RouteGuard from '../components/RouteGuard';
import ErrorBoundary from '../components/ErrorBoundary';

export const metadata = {
    title: 'Perioxia CRM — Leads, quotes, GST invoices, and payment',
    description: 'CRM for local service businesses. Capture leads from your website and WhatsApp, send quotes, invoice with GST, and collect payment on a 14-day trial.',
};

export default function RootLayout({ children }) {
    return (
        <html lang="en">
            <head>
                <link rel="preconnect" href="https://fonts.googleapis.com" />
                <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
            </head>
            <body className="antialiased">
                <ThemeProvider>
                    <LocaleProvider>
                        <AuthProvider>
                            <NotificationProvider>
                                <ChatProvider>
                                    <CallProvider>
                                        <SoftphoneProvider>
                                            <ErrorBoundary>
                                                <RouteGuard>
                                                    <Layout>
                                                        {children}
                                                    </Layout>
                                                </RouteGuard>
                                            </ErrorBoundary>
                                            <CallOverlay />
                                            <SoftphoneBar />
                                        </SoftphoneProvider>
                                    </CallProvider>
                                </ChatProvider>
                            </NotificationProvider>
                        </AuthProvider>
                    </LocaleProvider>
                </ThemeProvider>
            </body>
        </html>
    );
}
