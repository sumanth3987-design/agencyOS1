declare global {
  interface Window {
    Razorpay: any;
  }
}

export const triggerRazorpayCheckout = async (
  invoiceId: number,
  onSuccess: () => void,
  onError: (err: any) => void,
) => {
  try {
    const res = await fetch(`/api/invoices/${invoiceId}/pay`, {
      method: "POST",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to initiate checkout");

    const options = {
      key: data.razorpay_key,
      amount: data.amount,
      currency: data.currency,
      name: "AgencyOS",
      description: `Payment for Invoice #${data.invoice_number}`,
      order_id: data.order_id,
      handler: function () {
        onSuccess();
      },
      modal: {
        ondismiss: function () {
          console.log("Checkout window closed");
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