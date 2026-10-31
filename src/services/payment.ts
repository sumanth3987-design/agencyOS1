// Save this directly into your file: src/services/payment.ts

declare global {
  interface Window {
    Razorpay: any;
  }
}

interface RazorpaySuccessResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

/**
 * Loads the external Razorpay Checkout SDK dynamically if not already loaded.
 */
export const loadRazorpayScript = (): Promise<boolean> => {
  return new Promise((resolve) => {
    if (window.Razorpay) {
      resolve(true);
      return;
    }
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.async = true;
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.body.appendChild(script);
  });
};

/**
 * Trigger Razorpay checkout for Invoices with instant synchronous backend verification.
 */
export const triggerRazorpayCheckout = async (
  invoiceId: number,
  onSuccess: (paymentDetails?: any) => void,
  onError: (err: any) => void
) => {
  try {
    const isLoaded = await loadRazorpayScript();
    if (!isLoaded) {
      throw new Error("Razorpay SDK failed to load. Please check your internet connection.");
    }

    const res = await fetch(`/api/invoices/${invoiceId}/pay`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || data.message || "Failed to initiate invoice payment.");
    }

    const options = {
      key: data.razorpay_key,
      amount: data.amount,
      currency: data.currency || "INR",
      name: "AgencyOS",
      description: `Payment for Invoice #${data.invoice_number}`,
      order_id: data.order_id,
      handler: async function (response: RazorpaySuccessResponse) {
        try {
          // Immediately verify payment with backend
          const verifyRes = await fetch("/api/billing/verify-payment", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Accept": "application/json",
            },
            body: JSON.stringify({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });

          const verifyData = await verifyRes.json();
          if (!verifyRes.ok) {
            throw new Error(verifyData.error || verifyData.message || "Payment verification failed.");
          }

          onSuccess(verifyData);
        } catch (verifyErr) {
          onError(verifyErr);
        }
      },
      modal: {
        ondismiss: function () {
          console.log("Invoice payment window closed by user.");
        },
      },
      theme: { color: "#2563eb" },
    };

    const rzp = new window.Razorpay(options);
    rzp.open();
  } catch (err) {
    onError(err);
  }
};

/**
 * Trigger Razorpay checkout for Project milestone packages.
 */
export const triggerProjectPackageCheckout = async (
  projectId: number,
  onSuccess: (res: any) => void,
  onError: (err: any) => void
) => {
  try {
    const isLoaded = await loadRazorpayScript();
    if (!isLoaded) {
      throw new Error("Razorpay SDK failed to load.");
    }

    const res = await fetch(`/payment/create-order/${projectId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
    });

    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.message || "Failed to create project order.");
    }

    const options = {
      key: data.key_id,
      amount: data.amount,
      currency: data.currency || "INR",
      name: "AgencyOS",
      description: `Package deposit for Project #${projectId}`,
      order_id: data.order_id,
      handler: async function (response: RazorpaySuccessResponse) {
        try {
          const verifyRes = await fetch("/payment/verify", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Accept": "application/json",
            },
            body: JSON.stringify({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });

          const verifyData = await verifyRes.json();
          if (!verifyRes.ok || !verifyData.success) {
            throw new Error(verifyData.message || "Payment verification failed.");
          }

          onSuccess(verifyData);
        } catch (verifyErr) {
          onError(verifyErr);
        }
      },
      modal: {
        ondismiss: function () {
          console.log("Project payment window closed by user.");
        },
      },
      theme: { color: "#2563eb" },
    };

    const rzp = new window.Razorpay(options);
    rzp.open();
  } catch (err) {
    onError(err);
  }
};
