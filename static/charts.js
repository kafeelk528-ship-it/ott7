// if you want to move chart code out of template
function renderOrdersChart(labels, data) {
  const ctx = document.getElementById('ordersChart').getContext('2d');
  new Chart(ctx, {
    type: 'bar',
    data: { labels: labels, datasets: [{ label: 'Orders', data: data, backgroundColor: 'rgba(30,136,229,0.9)'}] },
    options: { responsive:true, maintainAspectRatio:false }
  });
}
